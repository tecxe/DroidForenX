"""Django management command: print the Android compatibility report.

Usage:
    python manage.py compat_report [--api 35] [--version 15] [--device SERIAL]

Query sources (first that applies):
    1. --api / --version flags
    2. --device: the stored API level of a registered device
    3. default: no query -> prints the table for all defined API levels
"""

from django.core.management.base import BaseCommand

from devices.models import Device
from devices.android_compatibility import (
    full_compatibility_report,
    summarize_report,
    TIER_LABELS,
    version_from_api,
)


class Command(BaseCommand):
    help = "Show the Android artifact compatibility matrix for API levels."

    def add_arguments(self, parser):
        parser.add_argument("--api", type=int, default=None, help="API level to report on.")
        parser.add_argument("--release", type=str, default=None, help="Android version to report on.")
        parser.add_argument("--device", type=str, default=None, help="Serial of a registered device.")

    def handle(self, *args, **options):
        api = options.get("api")
        version = options.get("release")
        serial = options.get("device")

        if version and api is None:
            from devices.android_compatibility import api_from_version
            api = api_from_version(version)

        if serial:
            try:
                dev = Device.objects.get(serial_number=serial)
            except Device.DoesNotExist:
                self.stderr.write(f"No device with serial {serial}.")
                return
            api = dev.api_level or api
            version = dev.android_version or version

        if api is None:
            self.stdout.write("Supported Matrix across API levels:")
            for level in (26, 28, 30, 31, 33, 34, 35):
                self._print_api(level)
            return

        self.stdout.write(self.style.SUCCESS(f"Compatibility for Android {version or version_from_api(api)} (API {api})"))
        self._print_api(api)

    def _print_api(self, api: int):
        self.stdout.write("")
        self.stdout.write(f"--- API {api} ({version_from_api(api)}) ---")
        summary = summarize_report(api)
        for tier_key, label in TIER_LABELS.items():
            items = summary.get(tier_key, [])
            self.stdout.write(f"\n  {label}:")
            for item in items:
                self.stdout.write(f"    - {item}")
            if not items:
                self.stdout.write("    (none)")