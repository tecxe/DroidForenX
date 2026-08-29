"""Device adapter registry.

Resolves the most specific available adapter for a manufacturer and falls
back to the generic Android adapter when no specialised adapter can handle a
device -- exactly the "manufacturer-agnostic with fallback" behaviour the
platform requires.

Specialised adapters are discovered per-manufacturer. A manufacturer that
cannot add forensic value simply routes to the generic adapter.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .base_adapter import BaseAdapter, DeviceInfo
from .samsung_adapter import SamsungAdapter
from .google_adapter import GoogleAdapter
from .generic_android_adapter import GenericAndroidAdapter

# Plugin registry keyed by manufacturer code. Kept as a dict so additional
# adapters can register without edits to the fallback logic.
_ADAPTER_CLASSES: Dict[str, type] = {
    "samsung": SamsungAdapter,
    "google": GoogleAdapter,
}


def register_adapter(manufacturer_code: str, adapter_cls: type) -> None:
    """Register (or override) the adapter class for a manufacturer code."""
    _ADAPTER_CLASSES[manufacturer_code.lower()] = adapter_cls


def known_manufacturers() -> List[str]:
    """Codes for which a specialised adapter is registered."""
    return sorted(_ADAPTER_CLASSES.keys())


def resolve_adapter(manufacturer_code: str) -> BaseAdapter:
    """Return an adapter instance for a manufacturer, generic as fallback."""
    code = (manufacturer_code or "generic").lower()
    cls = _ADAPTER_CLASSES.get(code, GenericAndroidAdapter)
    try:
        return cls()
    except TypeError:
        # Some adapters may require constructor args; fall back to generic.
        return GenericAndroidAdapter()


def list_all_adapters() -> List[BaseAdapter]:
    """Return one instance of every registered adapter plus the generic one."""
    adapters: List[BaseAdapter] = []
    seen: set = set()
    for code in _ADAPTER_CLASSES:
        inst = resolve_adapter(code)
        seen.add(type(inst).__name__)
        adapters.append(inst)
    generic = GenericAndroidAdapter()
    if type(generic).__name__ not in seen:
        adapters.append(generic)
    return adapters


def enumerate_all_devices() -> List[DeviceInfo]:
    """Enumerate visible devices across every available adapter.

    Results are de-duplicated by serial number; the most detailed record (the
    one carrying the most populated fields) is preferred for each device.
    """
    by_serial: Dict[str, DeviceInfo] = {}
    for adapter in list_all_adapters():
        if not getattr(adapter, "available", lambda: True)():
            continue
        for info in adapter.list_devices():
            if not info.serial_number:
                continue
            existing = by_serial.get(info.serial_number)
            if existing is None or _detail_score(info) > _detail_score(existing):
                by_serial[info.serial_number] = info
    return list(by_serial.values())


def get_device_detail(serial_number: str) -> DeviceInfo:
    """Resolve the best adapter for a device and return its detail.

    Manufacturer is not known up-front, so we query the generic (ADB) path to
    learn the manufacturer, then re-query with the specialised adapter if one
    exists. Falls back to generic on any failure.
    """
    from .adb_adapter import AdbAdapter

    probe = AdbAdapter()
    if not probe.available():
        return GenericAndroidAdapter().get_device(serial_number)

    try:
        base = probe.get_device(serial_number)
    except Exception:
        base = DeviceInfo(serial_number=serial_number, connection_state="unknown")

    adapter = resolve_adapter(base.manufacturer or "generic")
    try:
        return adapter.get_device(serial_number)
    except Exception:
        return base


def _detail_score(info: DeviceInfo) -> int:
    """Rough heuristic: more populated fields == more detail."""
    fields = (
        "manufacturer_raw",
        "model",
        "product",
        "android_version",
        "api_level",
        "build_number",
        "kernel_version",
        "security_patch",
    )
    return sum(1 for f in fields if getattr(info, f))
