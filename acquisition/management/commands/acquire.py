"""Django management command: run an authorized acquisition.

Usage:
    python manage.py acquire <case_number> [--serial SERIAL] [--level 1|2|3]
        [--method adb_logical|image_import] [--packages N] [--user USERNAME]

Runs the acquisition engine for a device against a case, records the
``AcquisitionRun`` + evidence chain, and writes chain-of-custody and audit
events. Identical to every other command, it reports honestly: if ADB is
unavailable or a target is not readable, the status reflects that.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from cases.models import Case
from custody.models import CustodyAction
from custody.service import log_event
from devices.models import Device
from devices.registry import get_device_detail
from devices.services import register_detected_device
from security.service import audit
from security.models import AuditEventType

from ...engine import acquire, AcquisitionError
from ...models import AcquisitionLevel


class Command(BaseCommand):
    help = "Run an authorized acquisition for a device on a case."

    def add_arguments(self, parser):
        parser.add_argument("case_number", type=str)
        parser.add_argument("--serial", type=str, default=None,
                            help="Device serial to acquire; detect if omitted.")
        parser.add_argument("--level", type=str, default=AcquisitionLevel.L1_LOGICAL,
                            choices=list(AcquisitionLevel.values))
        parser.add_argument("--method", type=str, default="adb_logical",
                            choices=["adb_logical", "image_import"])
        parser.add_argument("--packages", type=int, default=None,
                            help="Limit the number of packages to pull.")
        parser.add_argument("--user", type=str, default=None)

    def handle(self, *args, **options):
        try:
            case = Case.objects.get(case_number=options["case_number"])
        except Case.DoesNotExist:
            raise CommandError(f"Case {options['case_number']} not found.")

        user = None
        if options["user"]:
            user = get_user_model().objects.filter(username=options["user"]).first()

        device = self._resolve_device(options["serial"])

        try:
            run = acquire(
                case,
                device.serial_number if device else (options["serial"] or ""),
                device=device,
                level=options["level"],
                method=options["method"],
                operator=user,
                package_limit=options["packages"],
            )
        except AcquisitionError as exc:
            self.stdout.write(self.style.ERROR(f"Acquisition could not run: {exc}"))

            # Record the authoritative (from the failed run) evidence custody.
            run = self._latest_run(case, device)
            if run is not None and run.evidence_id:
                log_event(run.evidence, CustodyAction.FAILED,
                          actor=user, description=f"Acquisition failed: {exc}")
            audit(AuditEventType.FAILURE, actor=user, action="acquisition",
                  subject="case", detail=f"{case.case_number}: {exc}")
            return

        self.stdout.write(self.style.SUCCESS(f"Run {run.id} {run.summary()}"))
        for t in run.targets.all():
            self.stdout.write(
                f"  [{t.get_status_display()}] {t.target_type} {t.name}: {t.message}"
            )

        if run.evidence_id:
            log_event(run.evidence, CustodyAction.RECEIVED, actor=user,
                      description=f"Acquisition run {run.id} completed: {run.summary()}")
        audit(AuditEventType.WRITE, actor=user, action="acquisition",
              subject=case.case_number, detail=f"run {run.id} state={run.state} status={run.status}")

    def _resolve_device(self, serial):
        if serial:
            device = Device.objects.filter(serial_number=serial).first()
            if device:
                return device
            info = get_device_detail(serial)
            if info.serial_number or info.raw_properties:
                return register_detected_device(info)
            self.stdout.write(self.style.WARNING(
                f"Device {serial} not found/accessible; proceeding with serial only."))
            return None
        devices = Device.objects.all()
        if devices.exists():
            return devices.first()
        self.stdout.write(self.style.WARNING(
            "No device serial given and no registered devices; acquiring by empty serial."))
        return None

    def _latest_run(self, case, device):
        qs = case.acquisition_runs.all()
        if device:
            qs = qs.filter(device=device)
        return qs.order_by("-created").first()
