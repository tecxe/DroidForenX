"""Physical Acquisition — raw disk imaging and partition enumeration."""

import subprocess
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class RawImager:
    """Performs raw disk imaging via ADB for forensic acquisition."""

    def __init__(self, adb_path: str = "adb", serial: str = ""):
        self.adb_path = adb_path
        self.serial = serial

    def get_partitions(self) -> list:
        try:
            result = subprocess.run(
                [self.adb_path, "-s", self.serial, "shell", "ls", "-la", "/dev/block/bootdevice/by-name/"],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
            partitions = []
            for line in result.stdout.splitlines():
                if "->" in line:
                    parts = line.split()
                    if len(parts) >= 8:
                        name = parts[-3]
                        target = parts[-1]
                        partitions.append({"name": name, "block": target})
            return partitions
        except Exception:
            return []

    def get_disk_info(self) -> dict:
        try:
            result = subprocess.run(
                [self.adb_path, "-s", self.serial, "shell", "cat", "/proc/diskstats"],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
            return {"raw": result.stdout}
        except Exception:
            return {}
