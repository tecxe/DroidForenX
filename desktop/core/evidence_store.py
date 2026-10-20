"""
Forensic evidence store for DroidForenX.

Evidence acquired from a device is immutable. Once an artifact lands in
``acquired/`` it is never modified or deleted by analysis code. Parsers read
*from* this tree and write derived output to ``parsed/``, which is regenerable
and therefore never treated as evidence.

Layout, per case::

    <EVIDENCE_ROOT>/<case_id>/
        case.json              case metadata (id, operator, device, opened_at)
        acquired/              IMMUTABLE copies pulled from the device
        parsed/                derived exports - regenerable, not evidence
        manifests/
            SHA256SUMS          one "<sha256>  <path>" line per acquired artifact
            custody.jsonl       append-only chain-of-custody event log

Chain of custody is append-only by construction: every write opens the file in
append mode and flushes. Nothing in this module ever truncates, rewrites or
removes a custody record, and ``verify()`` re-hashes ``acquired/`` to detect
tampering after the fact.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, List, Optional

logger = logging.getLogger(__name__)

TOOL_NAME = "DroidForenX"
TOOL_VERSION = "2.0.0-p0"

EVIDENCE_ROOT = Path(os.environ.get("DROIDFORENX_EVIDENCE_ROOT") or (Path.home() / ".droidforenx" / "evidence"))

CHUNK = 1024 * 1024

# The case that loaders write into. Set by ``case()`` or ``set_active_case``.
_ACTIVE_CASE: Optional[str] = None


# --------------------------------------------------------------------------
# status engine
# --------------------------------------------------------------------------

class AcquisitionStatus:
    """
    The eight acquisition states DroidForenX reports. A loader may never claim
    success it did not achieve - every outcome maps to exactly one of these,
    and NOT_ACCESSIBLE always carries the reason it could not be reached.
    """

    ACQUIRED = "ACQUIRED"
    PARTIAL = "PARTIAL"
    RECOVERED = "RECOVERED"
    DELETED_RECOVERABLE = "DELETED_RECOVERABLE"
    DELETED_UNRECOVERABLE = "DELETED_UNRECOVERABLE"
    NOT_ACCESSIBLE = "NOT_ACCESSIBLE"
    NOT_PRESENT = "NOT_PRESENT"
    FAILED = "FAILED"

    ALL = (
        ACQUIRED, PARTIAL, RECOVERED, DELETED_RECOVERABLE,
        DELETED_UNRECOVERABLE, NOT_ACCESSIBLE, NOT_PRESENT, FAILED,
    )

    #: States that represent genuine, verifiable recovery.
    SUCCESSFUL = frozenset({ACQUIRED, PARTIAL, RECOVERED, DELETED_RECOVERABLE})
    #: States that mean "nothing was obtained, and we say why".
    UNSUCCESSFUL = frozenset({DELETED_UNRECOVERABLE, NOT_ACCESSIBLE, NOT_PRESENT, FAILED})

    @staticmethod
    def is_success(status: str) -> bool:
        return status in AcquisitionStatus.SUCCESSFUL


class VerificationTier:
    """
    How far a loader has actually been exercised. Recorded per loader so the
    tool never overstates what it has demonstrated.
    """

    T1_DEVICE_VERIFIED = "T1_DEVICE_VERIFIED"
    T2_FIXTURE_VERIFIED = "T2_FIXTURE_VERIFIED"
    T3_UNTESTED = "T3_UNTESTED"

    ALL = (T1_DEVICE_VERIFIED, T2_FIXTURE_VERIFIED, T3_UNTESTED)


@dataclass
class ArtifactRecord:
    """One immutable artifact in the evidence set."""
    artifact_id: str
    case_id: str
    remote_path: str
    local_path: str
    size_bytes: int
    sha256: str
    loader: str
    acquired_at: str
    status: str = AcquisitionStatus.ACQUIRED
    detail: str = ""


@dataclass
class CustodyEvent:
    """One append-only chain-of-custody entry."""
    timestamp: str
    case_id: str
    action: str
    actor: str
    detail: str = ""
    tool: str = TOOL_NAME
    tool_version: str = TOOL_VERSION


# --------------------------------------------------------------------------
# time / hashing helpers
# --------------------------------------------------------------------------

def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_jsonl(path: Path) -> List[dict]:
    """Read an append-only log, skipping any torn final line."""
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("Skipping malformed log line in %s", path.name)
    return entries


def record_verification(case_id: str, loader: str, tier: str,
                        evidence: str = "") -> dict:
    """
    Record that a loader has earned a verification tier.

    Tiers are never declared in a lookup table - they are earned, and the
    evidence for the claim is stored next to the claim so the claim itself is
    auditable. A loader with no entry in verification.jsonl is T3_UNTESTED,
    always. Declaring a tier is how a forensics tool starts lying.
    """
    if tier not in VerificationTier.ALL:
        raise ValueError(f"unknown verification tier: {tier!r}")
    if tier == VerificationTier.T1_DEVICE_VERIFIED and not evidence.strip():
        raise ValueError(
            "T1_DEVICE_VERIFIED requires evidence (device serial, OS build, "
            "date) - a device claim with no record is an unsupported assertion"
        )
    entry = {
        "timestamp": _utc_now(),
        "case_id": case_id,
        "loader": loader,
        "tier": tier,
        "evidence": evidence,
    }
    path = manifest_dir(case_id) / "verification.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    log_custody(case_id, "VERIFY", "droidforenx",
                f"{loader} claimed {tier} ({evidence or 'no evidence given'})")
    return entry


def get_verification_tier(case_id: str, loader: str) -> str:
    """The highest tier a loader has actually earned, else T3_UNTESTED."""
    order = [VerificationTier.T3_UNTESTED,
             VerificationTier.T2_FIXTURE_VERIFIED,
             VerificationTier.T1_DEVICE_VERIFIED]
    best = VerificationTier.T3_UNTESTED
    for entry in _read_jsonl(manifest_dir(case_id) / "verification.jsonl"):
        if entry.get("loader") != loader:
            continue
        tier = entry.get("tier")
        if tier in order and order.index(tier) > order.index(best):
            best = tier
    return best


def read_verification(case_id: str) -> List[dict]:
    return _read_jsonl(manifest_dir(case_id) / "verification.jsonl")


def hash_file(path: str | Path) -> str:
    """Stream a SHA-256 over a file without loading it into memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# case tree
# --------------------------------------------------------------------------

def _safe_component(value: str, fallback: str = "unassigned") -> str:
    """Reduce a string to something safe to use as a single path segment."""
    if not value:
        return fallback
    cleaned = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in str(value))
    cleaned = cleaned.strip("._")
    return cleaned[:64] or fallback


def case_dir(case_id: str) -> Path:
    return EVIDENCE_ROOT / _safe_component(case_id)


def acquired_dir(case_id: str) -> Path:
    return case_dir(case_id) / "acquired"


def parsed_dir(case_id: str) -> Path:
    return case_dir(case_id) / "parsed"


def manifest_dir(case_id: str) -> Path:
    return case_dir(case_id) / "manifests"


def ensure_case_tree(case_id: str) -> Path:
    """Create the full case directory structure if it does not already exist."""
    for directory in (acquired_dir(case_id), parsed_dir(case_id), manifest_dir(case_id)):
        directory.mkdir(parents=True, exist_ok=True)
    return case_dir(case_id)


def open_case(
    case_id: str,
    operator: str = "",
    device_serial: str = "",
    device_model: str = "",
    notes: str = "",
) -> Path:
    """
    Create (or re-open) a case, write its metadata, and log the opening event.
    Re-opening an existing case does not overwrite its original metadata.
    """
    root = ensure_case_tree(case_id)
    meta_path = root / "case.json"

    if not meta_path.exists():
        meta_path.write_text(
            json.dumps(
                {
                    "case_id": case_id,
                    "operator": operator,
                    "device_serial": device_serial,
                    "device_model": device_model,
                    "notes": notes,
                    "opened_at": _utc_now(),
                    "tool": TOOL_NAME,
                    "tool_version": TOOL_VERSION,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        log_custody(case_id, "CASE_OPENED", operator or "unknown",
                    f"serial={device_serial} model={device_model}")

    return root


# --------------------------------------------------------------------------
# append-only custody log
# --------------------------------------------------------------------------

def log_custody(case_id: str, action: str, actor: str, detail: str = "") -> None:
    """
    Append one event to the case custody log. Append-only: the file is opened
    with mode 'a' and flushed, so an existing record can never be overwritten.
    """
    try:
        manifest_dir(case_id).mkdir(parents=True, exist_ok=True)
        event = CustodyEvent(
            timestamp=_utc_now(),
            case_id=case_id,
            action=action,
            actor=actor or "unknown",
            detail=detail,
        )
        path = manifest_dir(case_id) / "custody.jsonl"
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(event)) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception as exc:  # custody logging must never break an acquisition
        logger.error(f"Custody log write failed for {case_id}: {exc}")


def read_custody(case_id: str) -> list[dict]:
    """Read the custody log in order. Safe to call at any time."""
    path = manifest_dir(case_id) / "custody.jsonl"
    if not path.exists():
        return []
    events = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning(f"Skipping malformed custody line in {path}")
    return events


# --------------------------------------------------------------------------
# acquisition + manifest
# --------------------------------------------------------------------------

def record_acquired(
    case_id: str,
    remote_path: str,
    local_path: str | Path,
    loader: str,
    status: str = AcquisitionStatus.ACQUIRED,
    detail: str = "",
    digest: Optional[str] = None,
) -> Optional[ArtifactRecord]:
    """
    Hash a freshly acquired file, add it to the SHA-256 manifest, and log a
    custody event. The file itself is left untouched - it is evidence.

    ``digest`` may be supplied when the caller already hashed the file during a
    verification pass (disk images are re-read to verify; hashing them again
    here would triple the I/O on a multi-gigabyte image). When omitted the file
    is hashed now, which is what every acquisition loader does.
    """
    path = Path(local_path)
    try:
        size = path.stat().st_size
        if digest is None:
            digest = hash_file(path)
    except OSError as exc:
        logger.error(f"Cannot hash acquired artifact {path}: {exc}")
        return None

    artifact_id = f"ART-{digest[:12]}"
    record = ArtifactRecord(
        artifact_id=artifact_id,
        case_id=case_id,
        remote_path=remote_path,
        local_path=str(path),
        size_bytes=size,
        sha256=digest,
        loader=loader,
        acquired_at=_utc_now(),
        status=status,
        detail=detail,
    )

    try:
        manifest_dir(case_id).mkdir(parents=True, exist_ok=True)
        with open(manifest_dir(case_id) / "artifacts.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(record)) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

        with open(manifest_dir(case_id) / "SHA256SUMS", "a", encoding="utf-8") as handle:
            # Reference the artifact by its path relative to the case root when
            # it lives inside it, and by absolute path otherwise. The old
            # hardcoded "acquired/<name>" claimed images kept outside the case
            # tree (e.g. a disk image in ~/.droidforenx/images) sat in acquired/.
            try:
                reference = path.resolve().relative_to(case_dir(case_id).resolve()).as_posix()
            except (ValueError, OSError):
                reference = path.resolve().as_posix()
            handle.write(f"{digest}  {reference}\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception as exc:
        logger.error(f"Manifest write failed for {artifact_id}: {exc}")

    log_custody(case_id, "ACQUIRED", loader,
                f"{artifact_id} {remote_path} sha256={digest[:16]}... ({size} bytes)")
    return record


def record_status(case_id: str, loader: str, status: str, detail: str = "") -> None:
    """
    Record the honest outcome of a loader. This is what stops a loader that
    recovered nothing from being presented as a success.
    """
    if status not in AcquisitionStatus.ALL:
        raise ValueError(f"Unknown acquisition status: {status!r}")
    try:
        manifest_dir(case_id).mkdir(parents=True, exist_ok=True)
        with open(manifest_dir(case_id) / "status.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "timestamp": _utc_now(),
                "case_id": case_id,
                "loader": loader,
                "status": status,
                "detail": detail,
            }) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception as exc:
        logger.error(f"Status write failed for {loader}: {exc}")

    if status not in AcquisitionStatus.SUCCESSFUL:
        log_custody(case_id, "STATUS", loader, f"{status}: {detail}")


def read_status(case_id: str) -> list[dict]:
    path = manifest_dir(case_id) / "status.jsonl"
    if not path.exists():
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def read_artifacts(case_id: str) -> list[dict]:
    """Read the append-only artifact log. Returns [] when nothing was acquired."""
    path = manifest_dir(case_id) / "artifacts.jsonl"
    if not path.exists():
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def write_parsed(case_id: str, name: str, payload: Any) -> Optional[Path]:
    """
    Write derived analysis output to parsed/. This is regenerable and is never
    hashed into the evidence manifest.
    """
    try:
        parsed_dir(case_id).mkdir(parents=True, exist_ok=True)
        target = parsed_dir(case_id) / (_safe_component(name, "output") + ".json")
        target.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return target
    except Exception as exc:
        logger.error(f"Failed writing parsed output {name}: {exc}")
        return None


def compute_integrity(case_id: str) -> dict:
    """
    Re-hash every acquired artifact and compare against the manifest.

    This is a pure read-only inspection: it never writes to the case tree and
    never appends a custody event, so reporting tools can call it safely.
    Use :func:`verify` when the run itself should be recorded in the log.
    """
    sums_path = manifest_dir(case_id) / "SHA256SUMS"
    if not sums_path.exists():
        return {"case_id": case_id, "checked": 0, "ok": 0, "mismatched": [], "missing": [],
                "actual": {}, "verdict": "NO_MANIFEST"}

    mismatched, missing = [], []
    #: relative path -> digest re-computed from the acquired file, or None when the
    #: artifact is absent from disk. Callers (e.g. the XLSX workbook) show this
    #: beside the manifest digest so the two can be compared at a glance.
    actual_hashes: dict[str, Optional[str]] = {}
    ok = 0
    with open(sums_path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            try:
                expected, relative = line.split("  ", 1)
            except ValueError:
                continue
            target = case_dir(case_id) / relative
            if not target.exists():
                missing.append(relative)
                actual_hashes[relative] = None
                continue
            actual = hash_file(target)
            actual_hashes[relative] = actual
            if actual == expected:
                ok += 1
            else:
                mismatched.append(relative)

    verdict = "VERIFIED" if not mismatched and not missing else "INTEGRITY_FAILURE"
    return {
        "case_id": case_id,
        "checked": ok + len(mismatched) + len(missing),
        "ok": ok,
        "mismatched": mismatched,
        "missing": missing,
        "actual": actual_hashes,
        "verdict": verdict,
    }


def verify(case_id: str) -> dict:
    """
    Re-hash every acquired artifact and compare against the manifest.
    This is the tamper-detection step an examiner or a defence would run.
    The run is recorded in the chain of custody.
    """
    result = compute_integrity(case_id)
    log_custody(case_id, "VERIFY", TOOL_NAME,
                f"{result['verdict']}: {result['ok']} ok, "
                f"{len(result['mismatched'])} mismatched, {len(result['missing'])} missing")
    return result


def export_case(case_id: str, destination: str | Path) -> Optional[Path]:
    """
    Copy the entire case tree to ``destination``. The export includes the
    custody log and manifests so the copy is independently verifiable.
    """
    source = case_dir(case_id)
    if not source.exists():
        return None

    target = Path(destination).resolve()
    root = EVIDENCE_ROOT.resolve()

    # Refuse to export onto evidence. A forensics tool that can be handed its
    # own evidence root as a destination would delete the case it is copying.
    if target == root or root in target.parents or target in source.resolve().parents:
        raise ValueError(
            f"refusing to export into the evidence tree ({target}); "
            "choose a destination outside EVIDENCE_ROOT"
        )
    if source.resolve() in target.parents:
        raise ValueError(f"refusing to export into the case itself ({target})")

    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    log_custody(case_id, "EXPORT", TOOL_NAME, str(target))
    return target


# --------------------------------------------------------------------------
# active-case context
# --------------------------------------------------------------------------

def set_active_case(case_id: Optional[str]) -> None:
    """Point loaders at a case. Pass None to clear the active case."""
    global _ACTIVE_CASE
    _ACTIVE_CASE = case_id
    if case_id:
        ensure_case_tree(case_id)


def get_active_case() -> Optional[str]:
    return _ACTIVE_CASE


@contextmanager
def case(
    case_id: str,
    operator: str = "",
    device_serial: str = "",
    device_model: str = "",
    notes: str = "",
) -> Iterator[str]:
    """
    Context manager that opens a case, makes it active for the duration of
    the block, and logs a recovery pass on exit.

        with evidence_store.case("CASE-2026-014", operator="Ofc. Mensah"):
            results = forensic_engine.run_examination(serial)
    """
    global _ACTIVE_CASE
    previous = _ACTIVE_CASE
    open_case(case_id, operator, device_serial, device_model, notes)
    _ACTIVE_CASE = case_id
    try:
        yield case_id
    finally:
        log_custody(case_id, "ANALYSIS_COMPLETE", operator or TOOL_NAME,
                    f"case={case_id} artifacts={len(list(acquired_dir(case_id).iterdir())) if acquired_dir(case_id).exists() else 0}")
        _ACTIVE_CASE = previous


def resolve_case(case_id: Optional[str] = None) -> str:
    """Return an explicit case id, else the active one, else 'unassigned'."""
    return case_id or _ACTIVE_CASE or "unassigned"