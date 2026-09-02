"""Device Manager — ADB device detection, fingerprinting, and state tracking."""

import subprocess
import re
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class DeviceInfo:
    serial: str = ""
    model: str = ""
    brand: str = ""
    android_version: str = ""
    api_level: str = ""
    build_id: str = ""
    security_patch: str = ""
    imei: str = ""
    sim_operator: str = ""
    network_operator: str = ""
    total_storage: str = ""
    available_storage: str = ""
    encryption_state: str = ""
    is_rooted: bool = False
    is_connected: bool = False


class DeviceManager:
    """Manages connected Android devices via ADB."""

    def __init__(self, adb_path: str = "adb"):
        self.adb_path = adb_path
        self.connected_devices: dict[str, DeviceInfo] = {}

    def detect_devices(self) -> list[DeviceInfo]:
        try:
            result = subprocess.run(
                [self.adb_path, "devices", "-l"],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
            devices = []
            for line in result.stdout.splitlines():
                if "device" in line and not line.startswith("List"):
                    parts = line.split()
                    if len(parts) >= 2 and parts[1] == "device":
                        serial = parts[0]
                        model = ""
                        for p in parts[2:]:
                            if p.startswith("model:"):
                                model = p.split(":", 1)[1]
                        devices.append(DeviceInfo(serial=serial, model=model, is_connected=True))
            return devices
        except Exception as e:
            logger.error(f"Device detection failed: {e}")
            return []

    def get_device_properties(self, serial: str) -> dict:
        props = {}
        keys = {
            "ro.product.model": "model",
            "ro.product.brand": "brand",
            "ro.build.version.release": "android_version",
            "ro.build.version.sdk": "api_level",
            "ro.build.display.id": "build_id",
            "ro.serialno": "serial",
        }
        for prop, key in keys.items():
            try:
                result = subprocess.run(
                    [self.adb_path, "-s", serial, "shell", "getprop", prop],
                    capture_output=True, text=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
                )
                props[key] = result.stdout.strip()
            except Exception:
                props[key] = ""
        return props
