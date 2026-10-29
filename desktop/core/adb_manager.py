"""Standalone ADB manager — no Django dependency."""
import os
import subprocess
import re
import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

def adb_executable_name(os_name: str | None = None) -> str:
    """
    Name of the adb executable for a platform: ``adb.exe`` on Windows, ``adb``
    everywhere else (Linux/Kali, macOS).

    ``os_name`` is injectable so the platform branches stay testable without
    mutating ``os.name`` (which pathlib consults at import time).
    """
    return "adb.exe" if (os_name or os.name) == "nt" else "adb"


def find_adb(exe_name: str | None = None) -> str:
    """
    Locate the ADB binary. Works on Windows, Linux (including Kali) and macOS.

    Order of preference:
      1. DROIDFORENX_ADB_PATH env override (explicit operator choice),
         with DROIDFORENX_ADB accepted as a legacy alias.
      2. Bundled copy shipped with the app / PyInstaller bundle.
      3. adb on the system PATH.
    """
    exe_name = exe_name or adb_executable_name()

    # DROIDFORENX_ADB_PATH is the documented name (see .env.example); the
    # shorter DROIDFORENX_ADB is kept as an alias so either spelling works.
    override = os.environ.get("DROIDFORENX_ADB_PATH") or os.environ.get("DROIDFORENX_ADB")
    if override:
        # Allow a bare executable name as well as a path, so a Linux operator
        # can set DROIDFORENX_ADB_PATH=adb without it resolving to nothing.
        resolved = shutil.which(override) or override
        if Path(resolved).exists():
            return resolved
        logger.warning(
            "ADB override %r not found; falling back to auto-detection", override
        )

    # When running as a PyInstaller one-file build, check _MEIPASS first.
    meipass = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and meipass:
        root = Path(meipass)
        for candidate in (
            root / "tools" / "platform-tools" / exe_name,
            root / "adb_bundle" / exe_name,
        ):
            if candidate.exists():
                return str(candidate)

    # Running from source.
    base = Path(__file__).resolve().parent
    for candidate in (
        base / "adb_bundle" / exe_name,
        base.parent / "tools" / "platform-tools" / exe_name,
        base.parent.parent / "tools" / "platform-tools" / exe_name,
    ):
        if candidate.exists():
            return str(candidate)

    # Finally defer to the system PATH (Kali ships adb via android-tools-adb).
    return shutil.which("adb") or exe_name


#: Executable name for the current platform.
ADB_EXE = adb_executable_name()
#: Resolved absolute or PATH-based ADB location.
ADB_PATH = find_adb()


@dataclass
class DeviceInfo:
    serial: str = ""
    model: str = ""
    manufacturer: str = ""
    device_name: str = ""
    android_version: str = ""
    api_level: int = 0
    security_patch: str = ""
    build_fingerprint: str = ""
    kernel_version: str = ""
    encryption_status: str = ""
    bootloader_state: str = ""
    sim_state: str = ""
    total_storage: int = 0
    free_storage: int = 0
    battery_level: int = 0
    battery_temp: float = 0.0
    connected: bool = False
    raw_props: dict = field(default_factory=dict)


def adb_cmd(args: list, timeout: int = 30) -> Tuple[bool, str]:
    """Run an ADB command and return (success, output)."""
    try:
        result = subprocess.run(
            [ADB_PATH] + args,
            capture_output=True, text=True, timeout=timeout,
            # The device speaks UTF-8. Letting Python pick the console code page
            # silently mangles contact names and message bodies into mojibake,
            # which then gets hashed into a report as if it were evidence.
            encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        # adb reports failures on stderr; fall back to stdout so callers get a reason.
        return False, (result.stderr.strip() or result.stdout.strip())
    except FileNotFoundError:
        logger.error(f"ADB binary not found at: {ADB_PATH}")
        return False, f"ADB binary not found at: {ADB_PATH}"
    except subprocess.TimeoutExpired:
        logger.warning(f"ADB command timed out: {' '.join(args)}")
        return False, "Command timed out"
    except Exception as e:
        logger.error(f"ADB command failed: {e}")
        return False, str(e)


def adb_shell(serial: str, command: str, timeout: int = 30) -> Tuple[bool, str]:
    """Run an ADB shell command on a specific device."""
    return adb_cmd(["-s", serial, "shell", command], timeout=timeout)


def capture_screen(serial: str, local_path: str, timeout: int = 45) -> Tuple[bool, str]:
    """Save a PNG screenshot of whatever is on the device screen.

    ``exec-out`` is used rather than ``shell`` because a shell redirect would
    translate CR/LF and corrupt the PNG on Windows, and the write goes straight
    to a binary file handle for the same reason. A screenshot is a finding in
    its own right -- the file's hash is what proves it later -- so an empty or
    non-PNG result is reported as a failure rather than saved.
    """
    destination = Path(local_path)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with open(destination, "wb") as handle:
            result = subprocess.run(
                [ADB_PATH, "-s", serial, "exec-out", "screencap", "-p"],
                stdout=handle, stderr=subprocess.PIPE, timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
    except subprocess.TimeoutExpired:
        return False, "Screenshot timed out"
    except FileNotFoundError:
        return False, f"ADB binary not found at: {ADB_PATH}"
    except Exception as exc:  # pragma: no cover - defensive
        return False, str(exc)

    if result.returncode != 0:
        destination.unlink(missing_ok=True)
        return False, result.stderr.decode("utf-8", "replace").strip() or "screencap failed"

    # PNG signature, and a floor on size: a dead device yields an empty file and
    # an empty "screenshot" is worse than an honest error message.
    try:
        with open(destination, "rb") as handle:
            signature = handle.read(8)
        size = destination.stat().st_size
    except OSError as exc:
        return False, str(exc)
    if signature != b"\x89PNG\r\n\x1a\n" or size < 1024:
        destination.unlink(missing_ok=True)
        return False, "device returned no usable image"
    return True, str(destination)


def list_devices() -> list:
    """
    List every ADB-attached device together with its state.

    Devices are NOT filtered out for being unauthorised or offline. Silently
    dropping them makes an unapproved phone look like an empty list, and an
    examiner concludes "no device" rather than "tap Allow USB debugging". The
    caller decides what to do with a non-ready device.
    """
    ok, output = adb_cmd(["devices", "-l"])
    if not ok:
        return []
    devices = []
    for line in output.splitlines()[1:]:
        line = line.strip()
        if not line or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        info = {}
        for part in parts[2:]:
            if ":" in part:
                k, v = part.split(":", 1)
                info[k] = v
        devices.append({
            "serial": serial,
            "state": state,
            "ready": state == "device",
            "model": info.get("model", ""),
            "product": info.get("product", ""),
            "device": info.get("device", ""),
            "transport_id": info.get("transport_id", ""),
        })
    return devices


def ready_devices() -> list:
    """Only devices that are authorised and ready for acquisition."""
    return [d for d in list_devices() if d["ready"]]


def describe_connection_state() -> str:
    """
    A human-readable explanation of why a device may not be usable.
    Returned to the UI so a stalled phone is diagnosed, not guessed at.
    """
    devices = list_devices()
    if not devices:
        return ("No device attached. Connect by USB, enable Developer options -> "
                "USB debugging, and set the USB mode to File Transfer (MTP).")
    notes = []
    for dev in devices:
        state = dev["state"]
        if state == "device":
            notes.append(f"{dev['serial']} ({dev['model'] or 'unknown model'}) is ready.")
        elif state == "unauthorized":
            notes.append(
                f"{dev['serial']} is UNAUTHORISED. Unlock the phone and accept the "
                "'Allow USB debugging' prompt, then reconnect."
            )
        elif state == "offline":
            notes.append(
                f"{dev['serial']} is offline. Unplug and replug the cable, or run "
                "'adb kill-server' then reconnect."
            )
        elif state == "recovery":
            notes.append(
                f"{dev['serial']} is in recovery mode - logical acquisition is not "
                "possible; use a dedicated acquisition procedure."
            )
        else:
            notes.append(f"{dev['serial']} reports state '{state}'.")
    return " ".join(notes)


def connect_device(serial: str) -> bool:
    """Ensure device is connected via ADB."""
    ok, _ = adb_cmd(["connect", serial])
    return ok


def get_device_info(serial: str) -> DeviceInfo:
    """Pull comprehensive device fingerprint via ADB."""
    info = DeviceInfo(serial=serial, connected=True)

    def prop(key: str) -> str:
        ok, val = adb_shell(serial, f"getprop {key}")
        return val.strip() if ok else ""

    info.model = prop("ro.product.model")
    info.manufacturer = prop("ro.product.manufacturer")
    info.device_name = prop("ro.product.device")
    info.android_version = prop("ro.build.version.release")
    api = prop("ro.build.version.sdk")
    try:
        info.api_level = int(api)
    except (ValueError, TypeError):
        info.api_level = 0
    info.security_patch = prop("ro.build.version.security_patch")
    info.build_fingerprint = prop("ro.build.fingerprint")
    info.bootloader_state = prop("ro.boot.verifiedbootstate")

    ok, kernel = adb_shell(serial, "uname -r")
    info.kernel_version = kernel if ok else ""

    enc = prop("ro.crypto.state")
    info.encryption_status = enc if enc else "unknown"

    ok, sim = adb_shell(serial, "getprop gsm.sim.state")
    info.sim_state = sim if ok else ""

    ok, storage = adb_shell(serial, "df /data | tail -1")
    if ok and storage:
        parts = storage.split()
        if len(parts) >= 4:
            try:
                info.total_storage = int(parts[1]) * 1024
                info.free_storage = int(parts[3]) * 1024
            except (ValueError, IndexError):
                pass
    if info.total_storage == 0:
        ok2, blk = adb_shell(serial, "cat /proc/emmc 2>/dev/null; cat /proc/partitions 2>/dev/null")
        if ok2 and blk:
            for line in blk.splitlines():
                p = line.split()
                if len(p) >= 4 and p[0].isdigit():
                    try:
                        info.total_storage = int(p[3]) * 1024
                    except ValueError:
                        pass
    if info.total_storage == 0:
        ok3, size_out = adb_shell(serial, "getprop ro.product.storage.size; getprop persist.sys.storage_size; cat /sys/class/block/mmcblk0/size 2>/dev/null")
        if ok3 and size_out:
            for line in size_out.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    val = int(line)
                    if val > 1000:
                        info.total_storage = val * 512
                        break
                except ValueError:
                    continue
    if info.total_storage == 0:
        ok4, props = adb_shell(serial, "getprop")
        if ok4:
            for line in props.splitlines():
                for key in ["ro.product.storage.size", "ro.emmc.size", "ro.product.internal_storage_size"]:
                    if key in line:
                        m = re.match(rf'\[{re.escape(key)}\]:\s*\[(.+?)\]', line)
                        if m:
                            try:
                                info.total_storage = int(m.group(1))
                            except ValueError:
                                pass

    ok, batt = adb_shell(serial, "dumpsys battery")
    if ok and batt:
        for line in batt.splitlines():
            if "level:" in line:
                try:
                    info.battery_level = int(line.split(":")[1].strip())
                except ValueError:
                    pass
            if "temperature:" in line:
                try:
                    info.battery_temp = int(line.split(":")[1].strip()) / 10.0
                except ValueError:
                    pass

    ok, props = adb_shell(serial, "getprop")
    if ok:
        for line in props.splitlines():
            m = re.match(r'\[(.+?)\]:\s*\[(.+?)\]', line)
            if m:
                info.raw_props[m.group(1)] = m.group(2)

    return info


def pm_list(serial: str, what: str = "packages", flags: str = "") -> tuple:
    """
    Run a ``pm list`` query scoped to the primary user.

    Plain ``pm list packages`` enumerates *every* Android user on the device.
    On any phone carrying a secondary or work profile the shell lacks
    permission for the other users and the command aborts with
    ``SecurityException: Shell does not have permission to access user 999``,
    returning error text instead of a package list. Scoping to user 0 - where
    the owner's own apps live - is what an examination of the primary user
    actually wants.

    Falls back to the unscoped form on builds that reject ``--user``.
    """
    scoped = f"pm list {what} --user 0 {flags}".strip()
    ok, out = adb_shell(serial, scoped, timeout=60)
    if ok and "Shell does not have permission" not in out:
        return ok, out

    unscoped = f"pm list {what} {flags}".strip()
    return adb_shell(serial, unscoped, timeout=60)


def get_installed_packages(serial: str) -> list:
    """List all installed packages with paths."""
    ok, output = pm_list(serial, "packages", "-f")
    if not ok or not output:
        return []
    packages = []
    for line in output.splitlines():
        if "package:" in line:
            pkg_line = line.replace("package:", "").strip()
            parts = pkg_line.split("=")
            if len(parts) == 2:
                packages.append({"package": parts[0], "apk_path": parts[1]})
    return packages


def pull_file(serial: str, remote: str, local: str, timeout: int = 120) -> bool:
    """Pull a file from device to local path."""
    Path(local).parent.mkdir(parents=True, exist_ok=True)
    ok, _ = adb_cmd(["-s", serial, "pull", remote, local], timeout=timeout)
    return ok


def pull_directory(serial: str, remote_dir: str, local_dir: str, timeout: int = 300) -> Tuple[int, int]:
    """Pull a directory tree. Returns (file_count, total_bytes)."""
    Path(local_dir).mkdir(parents=True, exist_ok=True)
    ok, _ = adb_cmd(["-s", serial, "pull", remote_dir + "/", local_dir], timeout=timeout)
    count = 0
    total = 0
    if ok:
        for f in Path(local_dir).rglob("*"):
            if f.is_file():
                count += 1
                total += f.stat().st_size
    return count, total


def query_content(serial: str, uri: str, sort: str = "", timeout: int = 60) -> str:
    """Query a content provider.

    ``timeout`` is exposed because the MediaStore index is huge on a phone with
    a full camera roll: a 60-second default there is a minute of silence per
    call, and three social loaders each making two calls can stall the whole
    extraction. Callers that only want a sample should ask for less.
    """
    cmd = f"content query --uri {uri}"
    if sort:
        cmd += f' --sort "{sort}"'
    ok, output = adb_shell(serial, cmd, timeout=timeout)
    return output if ok else ""


def enable_airplane(serial: str) -> Tuple[bool, str]:
    ok1, _ = adb_shell(serial, "settings put global airplane_mode_on 1")
    ok2, _ = adb_shell(serial, "am broadcast -a android.intent.action.AIRPLANE_MODE --ez state true")
    return ok1 and ok2, "airplane mode enabled"


def disable_wifi(serial: str) -> Tuple[bool, str]:
    ok, _ = adb_shell(serial, "svc wifi disable")
    return ok, "wifi disabled"


def disable_bluetooth(serial: str) -> Tuple[bool, str]:
    ok, _ = adb_shell(serial, "svc bluetooth disable")
    return ok, "bluetooth disabled"


def disable_mobile_data(serial: str) -> Tuple[bool, str]:
    ok, _ = adb_shell(serial, "svc data disable")
    return ok, "mobile data disabled"


def disable_nfc(serial: str) -> Tuple[bool, str]:
    ok, _ = adb_shell(serial, "svc nfc disable")
    return ok, "nfc disabled"


def enable_full_isolation(serial: str) -> list:
    """Enable all isolation measures. Returns list of (action, success, message)."""
    actions = []
    for name, func in [
        ("airplane_mode", enable_airplane),
        ("wifi", disable_wifi),
        ("bluetooth", disable_bluetooth),
        ("mobile_data", disable_mobile_data),
        ("nfc", disable_nfc),
    ]:
        ok, msg = func(serial)
        actions.append((name, ok, msg))
    return actions


def shell_find(serial: str, base_dir: str, name_patterns: list, timeout: int = 60) -> list:
    """Find files on device matching name patterns. Returns list of remote paths."""
    or_clause = " -o ".join(f'-name "{p}"' for p in name_patterns)
    ok, output = adb_shell(serial, f'find {base_dir} ( {or_clause} ) -type f 2>/dev/null', timeout=timeout)
    if not ok or not output:
        return []
    return [line.strip() for line in output.splitlines() if line.strip() and "Permission denied" not in line]


def shell_ls(serial: str, remote_dir: str, timeout: int = 15) -> list:
    """List files in a remote directory. Returns list of filenames."""
    ok, output = adb_shell(serial, f'ls -1 "{remote_dir}" 2>/dev/null', timeout=timeout)
    if not ok or not output:
        return []
    return [line.strip() for line in output.splitlines() if line.strip() and "Permission denied" not in line]


def shell_du(serial: str, remote_dir: str, timeout: int = 30) -> int:
    """Get directory size in bytes via du -sb."""
    ok, output = adb_shell(serial, f'du -sb "{remote_dir}" 2>/dev/null', timeout=timeout)
    if ok and output:
        parts = output.split()
        if parts:
            try:
                return int(parts[0])
            except ValueError:
                pass
    return 0


def pull_files_batch(serial: str, remote_files: list, local_base: str, timeout: int = 60) -> tuple:
    """Pull multiple files. Returns (success_count, total_bytes, errors)."""
    success = 0
    total_bytes = 0
    errors = []
    for remote in remote_files:
        local = str(Path(local_base) / remote.lstrip("/"))
        if pull_file(serial, remote, local, timeout=timeout):
            success += 1
            try:
                total_bytes += Path(local).stat().st_size
            except OSError:
                pass
        else:
            errors.append(remote)
    return success, total_bytes, errors


# ═════════════════════════════════════════════════════════════════════════════
#  ADVANCED FORENSIC EXTRACTION METHODS
# ═════════════════════════════════════════════════════════════════════════════

def shell_ls_recursive(serial: str, remote_dir: str, timeout: int = 120) -> list:
    """Full recursive ls -laR. Returns list of dicts with path, perms, size, date, name."""
    ok, out = adb_shell(serial, f'ls -laR "{remote_dir}" 2>/dev/null', timeout=timeout)
    if not ok or not out:
        return []

    entries = []
    current_dir = ""
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        # Directory header line (ends with ':')
        if line.endswith(":") and not line.startswith("-") and not line.startswith("d"):
            current_dir = line.rstrip(":")
            continue
        # Skip total lines
        if line.startswith("total "):
            continue
        # Parse file entry: perms links owner group size date time name
        parts = line.split(None, 8)
        if len(parts) < 9:
            continue
        perms = parts[0]
        size_str = parts[4]
        # Date is parts[5] + parts[6] + parts[7]
        date_str = f"{parts[5]} {parts[6]} {parts[7]}"
        name = parts[8]
        try:
            size = int(size_str)
        except ValueError:
            size = 0
        full_path = f"{current_dir}/{name}" if current_dir else name
        entries.append({
            "path": full_path,
            "name": name,
            "perms": perms,
            "size": size,
            "date": date_str,
            "is_dir": perms.startswith("d"),
        })
    return entries


def adb_backup(serial: str, package: str, local_path: str, timeout: int = 120) -> Tuple[bool, str]:
    """Attempt adb backup for a package. Returns (success, message_or_path).
    Note: Deprecated on API 31+, many apps will refuse. Returns False with reason on failure."""
    ok, out = adb_cmd(
        ["-s", serial, "backup", "-f", local_path, "-apk", package],
        timeout=timeout,
    )
    if ok and Path(local_path).exists() and Path(local_path).stat().st_size > 0:
        return True, local_path
    return False, out or "Backup failed or empty — app may not support backup on this API level"


def get_partition_list(serial: str) -> list:
    """Enumerate partitions from /proc/partitions and /dev/block/by-name/.
    Returns list of dicts with name, size_kb, block_path, type_guess."""
    partitions = []

    # Method 1: /proc/partitions
    ok, out = adb_shell(serial, "cat /proc/partitions 2>/dev/null", timeout=10)
    if ok and out:
        for line in out.splitlines()[2:]:  # Skip header
            parts = line.split()
            if len(parts) >= 4:
                name = parts[3]
                try:
                    size_kb = int(parts[2])
                except ValueError:
                    size_kb = 0
                partitions.append({
                    "name": name,
                    "size_kb": size_kb,
                    "size_mb": round(size_kb / 1024, 1),
                    "block_path": f"/dev/block/{name}",
                    "type_guess": _guess_partition_type(name),
                })

    # Method 2: /dev/block/by-name/ symlinks
    ok, out = adb_shell(serial, "ls -la /dev/block/by-name/ 2>/dev/null", timeout=10)
    if ok and out:
        for line in out.splitlines():
            parts = line.split(" -> ")
            if len(parts) == 2:
                link_name = parts[0].split()[-1] if parts[0].split() else ""
                target = parts[1].strip()
                if link_name:
                    # Update existing or add new
                    found = False
                    for p in partitions:
                        if p["name"] in target:
                            p["by_name"] = f"/dev/block/by-name/{link_name}"
                            p["type_guess"] = _guess_partition_type(link_name)
                            found = True
                            break
                    if not found:
                        partitions.append({
                            "name": link_name,
                            "size_kb": 0,
                            "size_mb": 0,
                            "block_path": target,
                            "by_name": f"/dev/block/by-name/{link_name}",
                            "type_guess": _guess_partition_type(link_name),
                        })

    return partitions


def _guess_partition_type(name: str) -> str:
    """Guess partition type from name."""
    name_lower = name.lower()
    if "boot" in name_lower and "recovery" not in name_lower:
        return "BOOT"
    elif "recovery" in name_lower:
        return "RECOVERY"
    elif "system" in name_lower:
        return "SYSTEM"
    elif "userdata" in name_lower or "data" in name_lower:
        return "USERDATA"
    elif "cache" in name_lower:
        return "CACHE"
    elif "metadata" in name_lower:
        return "METADATA"
    elif "misc" in name_lower:
        return "MISC"
    elif "vendor" in name_lower:
        return "VENDOR"
    elif "modem" in name_lower or "radio" in name_lower or "baseband" in name_lower:
        return "MODEM"
    elif "persist" in name_lower:
        return "PERSIST"
    elif "expdb" in name_lower or "proinfo" in name_lower:
        return "DEVICE_INFO"
    elif "nvram" in name_lower or "nvdata" in name_lower:
        return "NVRAM"
    elif "fat" in name_lower or "efs" in name_lower:
        return "EFS"
    return "UNKNOWN"


def shell_stat(serial: str, remote_path: str, timeout: int = 10) -> dict:
    """Stat a remote file. Returns dict with size, permissions, timestamps, inode."""
    ok, out = adb_shell(serial, f'stat "{remote_path}" 2>/dev/null', timeout=timeout)
    if not ok or not out:
        return {}

    result = {"path": remote_path}
    for line in out.splitlines():
        line = line.strip()
        if "Size:" in line or "size:" in line:
            try:
                result["size"] = int(line.split(":")[-1].strip().split()[0])
            except (ValueError, IndexError):
                pass
        elif "Access:" in line and "Uid:" not in line:
            # Access: 2026-08-15 10:30:45.123456789 +0000
            parts = line.split(":", 1)
            if len(parts) > 1:
                result["access_time"] = parts[1].strip()
        elif "Modify:" in line:
            parts = line.split(":", 1)
            if len(parts) > 1:
                result["modify_time"] = parts[1].strip()
        elif "Change:" in line:
            parts = line.split(":", 1)
            if len(parts) > 1:
                result["change_time"] = parts[1].strip()
        elif "Inode:" in line or "inode:" in line:
            try:
                result["inode"] = int(line.split(":")[-1].strip().split()[0])
            except (ValueError, IndexError):
                pass
        elif "Access:" in line and "Uid:" in line:
            # Access: (0755/drwxr-xr-x)  Uid: ( 1000/  system)   Gid: ( 1000/  system)
            perms_part = line.split("(")[1].split(")")[0] if "(" in line else ""
            if perms_part:
                result["permissions"] = perms_part
    return result


def shell_find_signatures(serial: str, base_dir: str, timeout: int = 120) -> list:
    """Find files by content signature (magic bytes) in a directory.
    Returns list of dicts with path, signature_type, size."""
    # Use find + file command for signature detection
    ok, out = adb_shell(
        serial,
        f'find "{base_dir}" -type f -exec file {{}} \\; 2>/dev/null',
        timeout=timeout,
    )
    if not ok or not out:
        return []

    signatures = []
    sig_map = {
        "JPEG": "JPEG image",
        "PNG": "PNG image",
        "GIF": "GIF image",
        "PDF": "PDF document",
        "SQLite": "SQLite database",
        "GZIP": "gzip compressed",
        "ZIP": "Zip archive",
        "RAR": "RAR archive",
        "MP4": "ISO Media, MP4",
        "MP3": "Audio file",
        "SQLite": "SQLite",
        "ELF": "ELF executable",
        "DEX": "Dalvik dex",
        "TAR": "tar archive",
        "BMP": "BMP image",
        "WEBP": "WebP image",
        "AVI": "RIFF video",
        "MKV": "Matroska",
        "TEXT": "ASCII text",
    }

    for line in out.splitlines():
        if ":" not in line:
            continue
        parts = line.split(":", 1)
        filepath = parts[0].strip()
        desc = parts[1].strip() if len(parts) > 1 else ""

        detected_type = "UNKNOWN"
        for sig_name, sig_desc in sig_map.items():
            if sig_desc.lower() in desc.lower():
                detected_type = sig_name
                break

        if detected_type != "UNKNOWN":
            # Get file size
            stat = shell_stat(serial, filepath, timeout=5)
            signatures.append({
                "path": filepath,
                "signature_type": detected_type,
                "file_description": desc,
                "size": stat.get("size", 0),
            })

    return signatures


def is_device_rooted(serial: str) -> bool:
    """
    Detect whether the device grants root to the adb shell user.

    Several loaders pull from app-private (``/data/data/<pkg>``) and keystore
    (``/data/misc/wifi``) paths that Android only exposes to root. The tool
    checks once per session so those loaders can report NOT_ACCESSIBLE with an
    accurate reason instead of returning an empty result that reads as success.

    Detection is deliberately conservative: any positive indication returns
    True, but an inconclusive probe returns False so we never overstate access.
    """
    probes = (
        "id",
        "su -c id",
        "/system/xbin/su -c id",
        "/system/bin/su -c id",
    )

    for probe in probes:
        ok, out = adb_shell(serial, probe, timeout=10)
        if not ok or not out:
            continue
        text = out.strip().lower()
        if "uid=0" in text:
            return True
        # Some su binaries prompt and time out; a bare "denied" still means no.
        if "not found" in text or "permission denied" in text:
            continue

    # Fall back to probing a known root-only path directly.
    ok, out = adb_shell(serial, "ls /data/data/ 2>/dev/null | head -n 1", timeout=10)
    if ok and out.strip() and "permission denied" not in out.lower():
        return True

    return False


def device_root_summary(serial: str) -> dict:
    """
    A reportable summary of what this device will and will not permit.
    Surfaced in the UI so an examiner knows the acquisition's ceiling before
    interpreting a result, rather than discovering it from an empty table.
    """
    try:
        info = get_device_info(serial)
    except Exception:
        info = None

    rooted = is_device_rooted(serial)
    model = getattr(info, "model", "") or ""
    manufacturer = getattr(info, "manufacturer", "") or ""

    return {
        "serial": serial,
        "model": model,
        "manufacturer": manufacturer,
        "android_version": getattr(info, "android_version", "") or "",
        "sdk_int": getattr(info, "api_level", 0) or 0,
        "encryption_status": getattr(info, "encryption_status", "") or "",
        "rooted": rooted,
        "can_reach_app_private_data": rooted,
        "can_reach_sdcard": True,
        "limitation": (
            None if rooted
            else "App-private data (/data/data/<pkg>) and saved WiFi keys "
                 "(/data/misc/wifi) are not readable without root. Loaders that "
                 "require them report NOT_ACCESSIBLE."
        ),
    }
