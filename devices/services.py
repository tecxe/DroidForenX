"""Device detection orchestration: ADB/adapters -> Device model."""

from __future__ import annotations

import logging
from typing import List

from django.utils import timezone

from .models import Device
from .base_adapter import DeviceInfo
from .registry import enumerate_all_devices, resolve_adapter

logger = logging.getLogger(__name__)


def device_info_to_model(info: DeviceInfo, device: Device) -> Device:
    """Apply a DeviceInfo onto a Device model instance (fields + JSON detail)."""
    device.manufacturer_raw = info.manufacturer_raw
    device.manufacturer = info.manufacturer or "generic"
    device.model = info.model
    device.product = info.product
    device.device_name = info.device_name
    device.android_version = info.android_version
    device.api_level = info.api_level
    device.build_number = info.build_number
    device.codename = info.codename
    device.cpu_architecture = info.cpu_architecture
    device.kernel_version = info.kernel_version
    device.security_patch = info.security_patch
    device.bootloader = info.bootloader
    device.ro_build_version_release = info.ro_build_version_release
    device.ro_product_model = info.ro_product_model
    device.encryption_enabled = info.encryption_enabled
    device.usb_debugging_enabled = info.usb_debugging_enabled
    if info.authorized is not None:
        device.authorized = info.authorized
    if info.connection_state:
        device.connection_state = info.connection_state
    device.identification = info.to_dict()
    return device


def register_detected_device(info: DeviceInfo) -> Device:
    """Create or update a Device row from a detected DeviceInfo."""
    device, created = Device.objects.get_or_create(
        serial_number=info.serial_number,
        defaults={
            "manufacturer": info.manufacturer or "generic",
            "manufacturer_raw": info.manufacturer_raw,
            "model": info.model,
            "android_version": info.android_version,
            "api_level": info.api_level,
            "connection_state": info.connection_state or "unknown",
            "identification": info.to_dict(),
        },
    )
    device = device_info_to_model(info, device)
    if created:
        device.first_seen = timezone.now()
    device.last_updated = timezone.now()
    device.save()
    return device


def scan_connected_devices() -> List[Device]:
    """Enumerate all visible devices and persist/refresh them in the DB.

    Returns the list of (newly, then updated) Device rows.
    """
    registered: List[Device] = []
    for info in enumerate_all_devices():
        if not info.serial_number:
            continue
        registered.append(register_detected_device(info))
    return registered


def resolve_adapter_for_device(model: Device):
    """Resolve the best adapter for an already-persisted device."""
    return resolve_adapter(model.manufacturer)
