"""ADB-based Android device detection adapter.

Interrogates devices through the official Android Debug Bridge interface.
All reads are legitimate, authorized ADB operations (``adb devices``,
``adb shell getprop``). No attempt is made to bypass authorization.

When ADB is not available (neither bundled nor on PATH) this adapter reports
it honestly via ``available()`` returning False, and ``list_devices`` returns
an empty list with an explanatory error rather than fabricating data.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from .base_adapter import BaseAdapter, DeviceInfo, DeviceNotFoundError
from .adapter_utils import (
    normalize_manufacturer,
    props_from_lines,
    yes_no_to_bool,
    try_int,
)
from .platform_tools import adb_binary, adb_available

logger = logging.getLogger(__name__)


class AdbAdapter(BaseAdapter):
    """Base ADB adapter handling arbitrary devices via ``adb shell getprop``."""

    manufacturer_code = "generic"
    display_name = "ADB Device Adapter"

    def __init__(self, adb_path: Optional[str] = None):
        self._adb = adb_path or adb_binary()

    # -- availability ----------------------------------------------------
    def available(self) -> bool:
        return self._adb is not None

    def adb_path(self) -> Optional[str]:
        return self._adb

    # -- process helpers -------------------------------------------------
    def _run(self, args, timeout: int = 20) -> Optional[List[str]]:
        """Run an adb command and return decoded stdout lines, or None on failure."""
        if not self._adb:
            return None
        import subprocess

        cmd = [self._adb] + args
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                errors="replace",
            )
        except (subprocess.TimeoutExpiredError, OSError) as exc:
            logger.debug("adb command failed: %s (%s)", cmd, exc)
            return None
        if proc.returncode != 0:
            logger.debug("adb command returned %s: %s", proc.returncode, proc.stderr.strip())
            return None
        return proc.stdout.splitlines()

    # -- device enumeration ----------------------------------------------
    def list_devices(self) -> List[DeviceInfo]:
        if not self.available():
            logger.info("ADB not available; no devices can be enumerated.")
            return []
        lines = self._run(["devices", "-l"], timeout=15)
        if not lines:
            return []
        devices: List[DeviceInfo] = []
        for line in lines:
            parts = line.split()
            if len(parts) < 2 or parts[0] == "List":
                continue
            serial, state = parts[0], parts[1]
            info = DeviceInfo(
                serial_number=serial,
                connection_state=state,
            )
            # Parse the extra metadata adb prints after the state token.
            for token in parts[2:]:
                if ":" in token:
                    key, _, value = token.partition(":")
                    value = value.replace("_", " ")
                    if key == "product":
                        info.product = value
                    elif key == "model":
                        info.model = value
                    elif key == "device":
                        info.device_name = value
                    elif key == "transport_id":
                        pass
                    elif key == "usb":
                        pass
            if state == "device":
                try:
                    info = self._populate_detail(info)
                except DeviceNotFoundError:
                    continue
            elif state == "unauthorized":
                info.authorized = False
                info.connection_state = "unauthorized"
            elif state == "offline":
                info.connection_state = "offline"
            devices.append(info)
        return devices

    # -- single device ---------------------------------------------------
    def get_device(self, serial_number: str) -> DeviceInfo:
        if not self.available():
            raise DeviceNotFoundError(f"ADB not available for device {serial_number}")
        # Confirm the device is present before querying.
        lines = self._run(["devices"], timeout=15) or []
        present = any(
            parts and parts[0] == serial_number for parts in (l.split() for l in lines if l)
        )
        if not present:
            raise DeviceNotFoundError(f"Device {serial_number} not present")
        return self._populate_detail(DeviceInfo(serial_number=serial_number))

    # -- property collection ----------------------------------------------
    def _populate_detail(self, info: DeviceInfo) -> DeviceInfo:
        """Fill in device properties for an authorized device."""
        props = self._getprop(info.serial_number)
        info.raw_properties = props
        self._apply_properties(info, props)
        return info

    def _getprop(self, serial: str) -> Dict[str, str]:
        """Read the full getprop key set for a device (system properties)."""
        lines = self._run(
            ["-s", serial, "shell", "getprop"], timeout=30
        )
        if not lines:
            return {}
        return props_from_lines(lines)

    # -- normalization ----------------------------------------------------
    def _apply_properties(self, info: DeviceInfo, props: Dict[str, str]):
        """Map the getprop key set onto normalized DeviceInfo fields."""
        def p(*keys, default: Optional[str] = None) -> Optional[str]:
            for key in keys:
                if key in props and props[key]:
                    return props[key]
            return default

        info.manufacturer_raw = p("ro.product.manufacturer", "ro.product.brand") or ""
        info.manufacturer = normalize_manufacturer(info.manufacturer_raw) or "generic"

        info.model = p("ro.product.model", "ro.product.name") or info.model
        info.product = p("ro.product.name", "ro.product.device") or info.product

        info.android_version = p(
            "ro.build.version.release", "ro.build.version.release_or_codename"
        ) or ""
        info.ro_build_version_release = p("ro.build.version.release") or ""
        info.api_level = try_int(p("ro.build.version.sdk"))
        info.build_number = p(
            "ro.build.display.id", "ro.build.id", "ro.build.version.incremental"
        ) or ""
        info.codename = p(
            "ro.build.version.codename", "ro.build.version.release_or_codename"
        ) or ""
        info.cpu_architecture = p(
            "ro.product.cpu.abi", "ro.product.cpu.abi2", "ro.arch"
        ) or ""
        info.kernel_version = p("ro.kernel.version") or ""
        info.security_patch = p("ro.build.version.security_patch") or ""
        info.bootloader = p("ro.bootloader", "ro.build.bootloader") or ""

        # Device name: try several keys; fallback to model.
        info.device_name = (
            p(
                "ro.product.device",
                "ro.product.marketname",
                "ro.product.name",
            )
            or info.device_name
            or info.model
        )

        ro_model = p("ro.product.model") or ""
        info.ro_product_model = ro_model

        # Security/encryption hints available from read-only properties.
        fde = p("ro.crypto.state")  # "encrypted" / "unencrypted"
        if fde is not None:
            info.encryption_enabled = fde.lower().strip() == "encrypted"

        # Whether debug settings allow debugging is reported by the device.
        info.usb_debugging_enabled = yes_no_to_bool(
            props.get("persist.sys.usb.config"), default=None
        )
        info.authorized = True  # state == "device" implies authorized at the ADB layer
        info.connection_state = "connected"

        # If nothing at all was read (e.g. device unresponsive over shell),
        # keep the serial but record the gap honestly.
        if not props:
            info.raw_properties["adapter_note"] = (
                "Device present but no properties could be read via adb shell."
            )
