"""DroidForenX device adapter base.

Defines the abstract contract that every device adapter must implement.

An adapter is responsible for:
  * enumerating currently visible Android devices, and
  * interrogating a single device to build its identification record.

The platform is manufacturer-agnostic: adapters register against a
manufacturer code and, when no specialised adapter can handle a device,
the generic Android adapter is used as a fallback.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class DeviceInfo:
    """Normalized, structured description of a detected Android device.

    Every field is best-effort; missing values are simply left at their
    default. No value is inferred -- anything here was actually read from
    the device (or reported by the platform invoking the adapter).
    """

    serial_number: str = ""
    manufacturer: str = ""          # normalized manufacturer code
    manufacturer_raw: str = ""      # raw string reported by the device
    model: str = ""
    product: str = ""
    device_name: str = ""
    android_version: str = ""
    api_level: Optional[int] = None
    build_number: str = ""
    codename: str = ""
    cpu_architecture: str = ""
    kernel_version: str = ""
    security_patch: str = ""
    bootloader: str = ""
    ro_build_version_release: str = ""
    ro_product_model: str = ""
    encryption_enabled: Optional[bool] = None
    usb_debugging_enabled: Optional[bool] = None
    authorized: Optional[bool] = None
    connection_state: str = "unknown"

    # Raw property map reported by the device (getprop / build.prop etc.).
    raw_properties: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Plain dict representation used for JSONField storage."""
        data = {
            "serial_number": self.serial_number,
            "manufacturer": self.manufacturer,
            "manufacturer_raw": self.manufacturer_raw,
            "model": self.model,
            "product": self.product,
            "device_name": self.device_name,
            "android_version": self.android_version,
            "api_level": self.api_level,
            "build_number": self.build_number,
            "codename": self.codename,
            "cpu_architecture": self.cpu_architecture,
            "kernel_version": self.kernel_version,
            "security_patch": self.security_patch,
            "bootloader": self.bootloader,
            "ro_build_version_release": self.ro_build_version_release,
            "ro_product_model": self.ro_product_model,
            "encryption_enabled": self.encryption_enabled,
            "usb_debugging_enabled": self.usb_debugging_enabled,
            "authorized": self.authorized,
            "connection_state": self.connection_state,
            "raw_properties": self.raw_properties,
        }
        return {k: v for k, v in data.items() if v not in (None, "", {})}


class BaseAdapter(abc.ABC):
    """Abstract base class for Android device adapters."""

    #: Manufacturer code this adapter handles, e.g. "samsung". Use "generic"
    #: for the catch-all adapter.
    manufacturer_code: str = "generic"

    #: Human readable name of the adapter.
    display_name: str = "Base Adapter"

    @abc.abstractmethod
    def list_devices(self) -> List[DeviceInfo]:
        """Return a list of currently visible devices.

        Returns an empty list if none are visible. Never raises for a single
        device failure; per-device errors are returned as DeviceInfo with an
        empty serial + an error recorded in ``raw_properties["error"]``.
        """

    @abc.abstractmethod
    def get_device(self, serial_number: str) -> DeviceInfo:
        """Return structured info for a single device.

        Raises DeviceNotFoundError when the device is not present.
        """

    def supports_manufacturer(self, manufacturer_code: str) -> bool:
        """Whether this adapter can handle the given normalized code."""
        return manufacturer_code.lower() == self.manufacturer_code

    def describe(self) -> dict:
        """Metadata about this adapter, for the UI / capabilities engine."""
        return {
            "code": self.manufacturer_code,
            "display_name": self.display_name,
            "class": f"{type(self).__module__}.{type(self).__name__}",
        }


class DeviceNotFoundError(Exception):
    """Raised when a requested device is not currently visible."""
