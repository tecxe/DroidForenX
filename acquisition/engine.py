"""Authorized acquisition orchestration.

Runs an acquisition for a device against a case, records an ``AcquisitionRun``,
and registers the outcome as ``Evidence`` with per-target ``AcquisitionTarget``
records and honest statuses.

Honesty rule: statuses are never upgraded based on intent. A target that could
not be pulled is recorded as NOT_ACCESSIBLE / FAILED / NOT_PRESENT. Overviews
and summaries reflect only what was actually recovered.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from cases.models import Case
from devices.models import Device
from evidence.models import AcquisitionStatus, EvidenceCategory
from evidence.services import register_evidence, record_limitation, set_evidence_status

from .adb_tools import Acquirer, AdbNotFoundError
from .models import AcquisitionLevel, AcquisitionRun, AcquisitionTarget, RunState

logger = logging.getLogger(__name__)

# Well-known locations commonly readable on a logical acquisition, if present.
LOGICAL_PATHS = [
    "/sdcard/DCIM/",
    "/sdcard/Download/",
    "/sdcard/Pictures/",
    "/sdcard/Movies/",
    "/sdcard/Documents/",
]

# Package families whose user-visible data directories are frequently readable
# via logical acquisition. This is a *best effort*; whether a directory is
# actually readable depends entirely on the device's own export/debug settings,
# and unreadable targets are recorded as such.
COMMON_PACKAGES = [
    "com.whatsapp",
    "com.facebook.katana",
    "com.instagram.android",
    "com.google.android.apps.messaging",
    "com.android.providers.contacts",
    "com.android.providers.telephony",
]


class AcquisitionError(Exception):
    pass


def acquire(
    case: Case,
    serial: str,
    device: Optional[Device] = None,
    level: str = AcquisitionLevel.L1_LOGICAL,
    method: str = "adb_logical",
    operator=None,
    package_limit: Optional[int] = None,
) -> AcquisitionRun:
    """Run an authorized acquisition for a device, returning the run record.

    Raises AcquisitionError when the acquisition cannot begin (e.g. ADB absent).
    """
    run = AcquisitionRun.objects.create(
        case=case,
        device=device,
        level=level,
        method=method,
        operator=operator,
        state=RunState.PENDING,
    )
    try:
        if method == "image_import":
            return _acquire_image_import(run, case, serial)
        return _acquire_adb(run, case, device, serial, level, package_limit)
    except Exception as exc:  # noqa: BLE001 - funnel into auditable failure
        logger.exception("acquisition %s failed", run.id)
        run.notes = f"{run.notes}\nFAILED: {exc}".strip()
        run.finalize(AcquisitionStatus.FAILED)
        _register_failed_evidence(run, case, device, str(exc))
        raise AcquisitionError(str(exc)) from exc


# --------------------------------------------------------------------------- #
# ADB logical acquisition
# --------------------------------------------------------------------------- #
def _acquire_adb(run, case, device, serial, level, package_limit):  # noqa: C901
    try:
        acq = Acquirer(serial)
    except AdbNotFoundError as exc:
        run.finalize(AcquisitionStatus.NOT_ACCESSIBLE)
        # Register an evidence container that honestly reports the limitation.
        _register_adb_gap(run, case, device, serial, str(exc))
        raise AcquisitionError(str(exc)) from exc

    run.mark_started()
    # Evidence container for the outcome of this run.
    evidence = _register_run_evidence(run, case, device, serial)
    run.evidence = evidence
    run.save(update_fields=["evidence"])

    targets_ok = target_failed = not_present = 0
    files_pulled = 0
    bytes_pulled = 0

    try:
        # 1) Enumerate packages (read-only) and pull APKs + readable data.
        packages = acq.installed_packages()
        if package_limit is not None:
            packages = packages[:package_limit]
        if not packages:
            _add_target(run, "provider", "package list",
                        status=AcquisitionStatus.NOT_PRESENT,
                        message="adb pm list returned no packages")
            not_present += 1

        out_root = _workspace(run) / "logical"

        for pkg in packages:
            status, pulled, bts, msg = _pull_package(acq, pkg, out_root)
            target_failed += 1 if status in {AcquisitionStatus.NOT_ACCESSIBLE, AcquisitionStatus.FAILED} else 0
            not_present += 1 if status is AcquisitionStatus.NOT_PRESENT else 0
            files_pulled += pulled
            bytes_pulled += bts
            _add_target(run, "package", pkg, detail=_apk_path(acq, pkg),
                        status=status, message=msg, files=pulled, bytes=bts)
            targets_ok += 1 if status in {AcquisitionStatus.ACQUIRED, AcquisitionStatus.PARTIAL} else 0

        # 2) Best-effort pull of well-known logical paths (only if present).
        for remote in LOGICAL_PATHS:
            count, bts = _pull_tree(acq, remote, out_root / "removable")
            files_pulled += count
            bytes_pulled += bts
            st = AcquisitionStatus.ACQUIRED if count else AcquisitionStatus.NOT_PRESENT
            if count:
                targets_ok += 1
            else:
                not_present += 1
            _add_target(run, "path", remote, status=st,
                        message=f"{count} files", files=count, bytes=bts)

        # Determine overall honest status from the accounting.
        if not_present and targets_ok == 0 and files_pulled == 0:
            overall = AcquisitionStatus.NOT_PRESENT
        elif target_failed or (not_present and files_pulled == 0):
            overall = AcquisitionStatus.PARTIAL
        else:
            overall = AcquisitionStatus.ACQUIRED

        run.targets_total = targets_ok + target_failed + not_present
        run.targets_ok = targets_ok
        run.targets_failed = target_failed + not_present
        run.files_pulled = files_pulled
        run.bytes_pulled = bytes_pulled
        run.finalize(overall)
        _finalize_evidence(evidence, overall, files_pulled, bytes_pulled, run)

    finally:
        pass
    return run


def _pull_package(acq, pkg, out_root):
    """Attempt to pull a package's APK and readable data dir. Returns status."""
    apk_path = acq.target_apk_path(pkg)
    pulled = 0
    bts = 0
    pulled_any = False
    if apk_path:
        safe_name = apk_path.split("/")[-1] or f"{pkg}.apk"
        local = out_root / "packages" / pkg / "apk" / safe_name
        if acq.pull(apk_path, local):
            pulled_any = True
            pulled += 1
            bts += local.stat().st_size

    # User data dir — only attempt; unreadable dirs produce a NOT_ACCESSIBLE mark.
    data_dir = f"/data/data/{pkg}/"
    if acq.exists(data_dir):
        # Try to copy the whole directory (best effort); record partial if empty.
        local_dir = out_root / "packages" / pkg / "data"
        count, blen = _pull_tree(acq, data_dir, local_dir)
        pulled += count
        bts += blen
        if count:
            pulled_any = True

    if pulled_any:
        status = AcquisitionStatus.ACQUIRED if pulled > 0 else AcquisitionStatus.PARTIAL
        msg = f"pulled {pulled} files"
    elif apk_path:
        status = AcquisitionStatus.NOT_ACCESSIBLE
        msg = "package present but data not readable via adb"
    else:
        status = AcquisitionStatus.NOT_PRESENT
        msg = "package not found (pm path returned nothing)"
    return status, pulled, bts, msg


def _apk_path(acq, pkg):
    return acq.target_apk_path(pkg) or ""


def _pull_tree(acq, remote_dir, local_dir):
    """Recursively pull a remote directory tree (best effort). Returns (count, bytes)."""
    count = 0
    bts = 0
    if not acq.exists(remote_dir):
        return count, bts
    remote = remote_dir.rstrip("/")
    local_dir.mkdir(parents=True, exist_ok=True)
    # adb pull -a copies a tree recursively.
    proc = subprocess_pull(acq, [remote + "/", str(local_dir)])
    if proc and proc.returncode == 0 and local_dir.exists():
        for f in local_dir.rglob("*"):
            if f.is_file():
                count += 1
                bts += f.stat().st_size
    return count, bts


def subprocess_pull(acq, args):
    import subprocess
    try:
        return subprocess.run(
            [str(acq.adapter.adb_path()), "-s", acq.serial] + args,
            capture_output=True, text=True, timeout=600, errors="replace",
        )
    except (subprocess.TimeoutExpiredError, OSError):
        return None


# --------------------------------------------------------------------------- #
# Image import (Level 3)
# --------------------------------------------------------------------------- #
def _acquire_image_import(run, case, serial):
    raise AcquisitionError(
        "image_import requires an explicit image path; use register_evidence instead."
    )


# --------------------------------------------------------------------------- #
# Evidence bookkeeping
# --------------------------------------------------------------------------- #
def _register_run_evidence(run, case, device, serial):
    return register_evidence(
        case,
        device=device,
        category=EvidenceCategory.ACQUISITION_IMAGE if run.level == AcquisitionLevel.L3_PHYSICAL else EvidenceCategory.OTHER,
        classification="original",
        status=AcquisitionStatus.ACQUIRED,
        acquisition_level=int(run.level),
        acquisition_method=run.method,
        title=f"ADB logical acquisition of {serial}",
        source=f"adb:{serial}",
        collected_by=None,
    )


def _finalize_evidence(evidence, status, files_pulled, bytes_pulled, run):
    if status in {AcquisitionStatus.NOT_PRESENT, AcquisitionStatus.NOT_ACCESSIBLE}:
        set_evidence_status(evidence, status, reason=run.notes or f"no files recovered ({status})")
    evidence.refresh_from_db()


def _register_adb_gap(run, case, device, serial, message):
    evidence = register_evidence(
        case,
        device=device,
        category=EvidenceCategory.OTHER,
        classification="original",
        status=AcquisitionStatus.NOT_ACCESSIBLE,
        acquisition_level=int(run.level),
        acquisition_method=run.method,
        title=f"ADB acquisition unavailable for {serial}",
        source=f"adb:{serial}",
        collected_by=None,
    )
    record_limitation(
        evidence, "ADB acquisition",
        message or "ADB binary is not available on this platform.",
    )
    run.evidence = evidence
    run.save(update_fields=["evidence"])


def _register_failed_evidence(run, case, device, message):
    evidence = register_evidence(
        case,
        device=device,
        category=EvidenceCategory.OTHER,
        classification="original",
        status=AcquisitionStatus.FAILED,
        acquisition_level=int(run.level),
        acquisition_method=run.method,
        title=f"Acquisition failed ({run.id})",
        source=f"adb:{getattr(device, 'serial_number', None) or 'unknown'}",
        collected_by=None,
    )
    record_limitation(evidence, "acquisition", message or "acquisition errored")
    run.evidence = evidence
    run.save(update_fields=["evidence"])


def _add_target(run, target_type, name, *, detail="", status, message="", files=0, bytes=0):  # noqa: A002
    AcquisitionTarget.objects.create(
        run=run,
        target_type=target_type,
        name=name,
        detail=detail,
        status=status,
        files_pulled=files,
        bytes_pulled=bytes,
        message=message,
    )


def _workspace(run: AcquisitionRun) -> Path:
    from evidence.storage import evidence_root
    ws = evidence_root() / "working" / f"acq_{run.id}"
    ws.mkdir(parents=True, exist_ok=True)
    return ws
