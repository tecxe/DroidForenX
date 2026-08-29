"""ADB-mediated authorized logical acquisition helpers.

These operate only over the official Android Debug Bridge, reading what the
debug protocol exposes on an authorized device. This layer never attempts to
bypass authorization, never roots, and never touches credential material; it
simply copies the files/packages that the debug interface makes readable.

All functions report ``None``/empty and a reason when ADB is unavailable so
the caller can record an honest NOT_ACCESSIBLE/NOT_PRESENT status instead of
fabricating data.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from devices.adb_adapter import AdbAdapter
from devices.platform_tools import adb_binary, adb_available

logger = logging.getLogger(__name__)


class AdbNotFoundError(Exception):
    """Raised when adb is not available for an acquisition."""


class Acquirer:
    """Thin wrapper over the device ADB adapter for pull-style acquisition."""

    def __init__(self, serial: str, adb_path: Optional[str] = None):
        self.serial = serial
        self.adapter = AdbAdapter(adb_path or adb_binary())
        if not self.adapter.available():
            raise AdbNotFoundError("ADB is not available; cannot acquire.")

    # -- primitives -------------------------------------------------------
    def _run(self, args, timeout: int = 60) -> Optional[List[str]]:
        return self.adapter._run(["-s", self.serial] + args, timeout=timeout)

    def shell(self, command: str, timeout: int = 60) -> Optional[List[str]]:
        """Run an adb shell command, returning lines or None on failure."""
        return self._run(["shell", command], timeout=timeout)

    def pull(self, remote: str, local: Path, timeout: int = 300) -> bool:
        """Copy a remote path to a local file. Returns True on success."""
        local.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [str(self.adapter.adb_path()), "-s", self.serial, "pull", remote, str(local)],
            capture_output=True,
            text=True,
            timeout=timeout,
            errors="replace",
        )
        if proc.returncode != 0:
            logger.info("adb pull failed for %s: %s", remote, proc.stderr.strip())
            return False
        return local.exists() and local.stat().st_size >= 0

    # -- queries ----------------------------------------------------------
    def installed_packages(self) -> List[str]:
        """Return the list of package names installed on the device (read-only)."""
        lines = self.shell("pm list packages -f") or []
        pkgs: List[str] = []
        for line in lines:
            line = line.strip()
            if not line.startswith("package:"):
                continue
            # format: package:/path/pkg.apk=com.example.app
            self._separator = "="
            if "=" in line:
                pkgs.append(line.partition("=")[2].strip())
        return pkgs

    def target_apk_path(self, package: str) -> Optional[str]:
        """Find the on-device APK path for a package, if any."""
        lines = self._run(["shell", "pm path", package]) or []
        for line in lines:
            line = line.strip()
            if line.startswith("package:"):
                return line[len("package:"):].strip()
        return None

    def readable_files(self, remote_dir: str) -> List[str]:
        """List files under a remote directory that the shell can see."""
        lines = self.shell(f"ls -la {remote_dir}") or []
        result: List[str] = []
        for line in lines:
            parts = line.split()
            # ls -la listing: perms links owner group size mon day time name
            if len(parts) >= 9 and parts[0].startswith(("d", "-", "l", "b", "c")):
                name = parts[-1]
                if name in (".", ".."):
                    continue
                is_dir = parts[0].startswith("d")
                record = name + "/" if is_dir else name
                result.append(record)
        return result

    def exists(self, path: str) -> bool:
        """Whether a remote path exists (best effort)."""
        out = self.shell(f"ls {path}", timeout=20)
        # adb returns blank/None on failure.
        return bool(out) and any(line.strip() for line in out)
