"""Django management command: detect connected Android devices.

Usage:
    python manage.py detect_devices [--serial SERIAL]

Without --serial, enumerates every visible device and persists them.
With --serial, interrogates a single device by its serial number.

This is a read-only, authorized detection pass; it never modifies a device.
"""

from django.core.management.base import BaseCommand, CommandError

from devices.platform_tools import adb_binary, adb_available
from devices.services import register_detected_device, scan_connected_devices
from devices.registry import get_device_detail


class Command(BaseCommand):
    help = "Detect connected Android devices and register them in the platform."

    def add_arguments(self, parser):
        parser.add_argument(
            "--serial",
            type=str,
            default=None,
            help="Interrogate a single device by serial number.",
        )

    def handle(self, *args, **options):
        serial = options.get("serial")

        if not adb_available():
            self.stdout.write(
                self.style.WARNING(
                    "ADB is not available (no bundled platform-tools and not on PATH). "
                    "Run setup_adb.py to bundle ADB, or install platform-tools. "
                    "The platform remains usable for manual evidence import."
                )
            )

        if serial:
            info = get_device_detail(serial)
            if not info.serial_number and not info.raw_properties:
                raise CommandError(f"Device {serial} not found / not accessible.")
            device = register_detected_device(info)
            self.stdout.write(
                self.style.SUCCESS(f"Registered device: {device.display_name} ({device.serial_number})")
            )
            return

        devices = scan_connected_devices()
        adb = adb_binary()
        self.stdout.write(
            self.style.SUCCESS(f"ADB: {adb or 'unavailable'} | {len(devices)} device(s) registered/updated")
        )
        for d in devices:
            self.stdout.write(f"  - {d.display_name}  [{d.connection_state}]")
        if not devices:
            self.stdout.write("No devices detected. Connect an authorized device with USB debugging enabled.")
