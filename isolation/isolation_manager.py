"""Device Isolation — network capture, airplane mode, and forensic isolation."""

import subprocess
import logging

logger = logging.getLogger(__name__)


class IsolationManager:
    """Manages device isolation procedures for forensic acquisition."""

    def __init__(self, adb_path: str = "adb", serial: str = ""):
        self.adb_path = adb_path
        self.serial = serial

    def enable_airplane_mode(self) -> bool:
        return self._set_airplane(True)

    def disable_airplane_mode(self) -> bool:
        return self._set_airplane(False)

    def _set_airplane(self, enable: bool) -> bool:
        value = "1" if enable else "0"
        try:
            subprocess.run(
                [self.adb_path, "-s", self.serial, "shell",
                 "settings", "put", "global", "airplane_mode_on", value],
                capture_output=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
            return True
        except Exception as e:
            logger.error(f"Airplane mode toggle failed: {e}")
            return False

    def disable_wifi(self) -> bool:
        try:
            subprocess.run(
                [self.adb_path, "-s", self.serial, "shell", "svc", "wifi", "disable"],
                capture_output=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
            return True
        except Exception:
            return False

    def disable_mobile_data(self) -> bool:
        try:
            subprocess.run(
                [self.adb_path, "-s", self.serial, "shell", "svc", "data", "disable"],
                capture_output=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
            return True
        except Exception:
            return False

    def get_network_interfaces(self) -> list:
        try:
            result = subprocess.run(
                [self.adb_path, "-s", self.serial, "shell", "ip", "addr", "show"],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
            interfaces = []
            for line in result.stdout.splitlines():
                if line.startswith(" ") and ":" in line:
                    parts = line.split()
                    if len(parts) >= 2:
                        interfaces.append(parts[1].rstrip(":"))
            return interfaces
        except Exception:
            return []
