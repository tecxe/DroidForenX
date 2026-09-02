"""Security Module — encryption detection and device security state analysis."""

import subprocess
import logging

logger = logging.getLogger(__name__)


class EncryptionDetector:
    """Detects device encryption state and security configurations."""

    def __init__(self, adb_path: str = "adb", serial: str = ""):
        self.adb_path = adb_path
        self.serial = serial

    def get_encryption_state(self) -> dict:
        state = {}
        try:
            result = subprocess.run(
                [self.adb_path, "-s", self.serial, "shell", "getprop", "ro.crypto.state"],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
            state["encryption"] = result.stdout.strip()
        except Exception:
            state["encryption"] = "unknown"

        try:
            result = subprocess.run(
                [self.adb_path, "-s", self.serial, "shell", "getprop", "ro.crypto.type"],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
            state["encryption_type"] = result.stdout.strip()
        except Exception:
            state["encryption_type"] = "unknown"
        return state

    def get_lock_screen_state(self) -> dict:
        state = {}
        try:
            result = subprocess.run(
                [self.adb_path, "-s", self.serial, "shell", "dumpsys", "window"],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
            for line in result.stdout.splitlines():
                if "mDreamingLockscreen" in line:
                    state["lockscreen"] = "locked" if "true" in line.lower() else "unlocked"
                if "mShowingLockscreen" in line:
                    state["showing_lockscreen"] = "true" in line.lower()
        except Exception:
            pass
        return state

    def get_verified_boot_state(self) -> str:
        try:
            result = subprocess.run(
                [self.adb_path, "-s", self.serial, "shell", "getprop", "ro.boot.verifiedbootstate"],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
            return result.stdout.strip()
        except Exception:
            return "unknown"
