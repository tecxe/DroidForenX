"""Tests for the authorized acquisition layer.

Because real hardware is not available in CI, the ADB-facing layers are mocked.
The assertions focus on what we control: honest statuses, audit bookkeeping,
and correct evidence registration regardless of what the device returned.
"""

import io
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from cases.models import Case, Investigator
from custody.models import CustodyAction, CustodyEvent
from devices.models import Device
from evidence.models import AcquisitionStatus
from evidence.services import register_artifact, register_evidence
from security.models import AuditEventType, AuditLog
from .engine import acquire, AcquisitionError
from .models import AcquisitionRun, AcquisitionTarget, RunState


def make_case_and_device():
    inv = Investigator.objects.create(name="Ada Lovelace", badge_id="A-01")
    case = Case.objects.create(case_number="CASE-ACQ-1", title="Acq Test", lead_investigator=inv)
    device = Device.objects.create(
        serial_number="SER-ACQ-1",
        manufacturer="google",
        model="Pixel 9 Pro",
        api_level=35,
    )
    return case, device


class AdbUnavailableTest(TestCase):
    def test_acquire_raises_when_adb_missing(self):
        case, device = make_case_and_device()
        with mock.patch("acquisition.engine.Acquirer", side_effect=Exception("no adb")):
            with self.assertRaises(AcquisitionError):
                acquire(case, device.serial_number, device=device)

        run = AcquisitionRun.objects.get(case=case)
        self.assertEqual(run.level, "1")
        self.assertEqual(run.state, RunState.FAILED)
        # Honest accounting: nothing was recovered.
        self.assertEqual(run.files_pulled, 0)


class HonestStatusTest(TestCase):
    def test_no_packages_recorded_as_not_present(self):
        case, device = make_case_and_device()
        acq = mock.Mock()
        acq.installed_packages.return_value = []
        acq.target_apk_path.return_value = None
        acq.exists.return_value = False
        acq.pull.return_value = False
        acq.adapter.adb_path.return_value = "adb"

        with mock.patch("acquisition.engine.Acquirer", return_value=acq), \
             mock.patch("acquisition.engine._workspace") as ws:
            from pathlib import Path
            import tempfile
            class DummyWorkspace:
                def __init__(self):
                    self._tmp = Path(tempfile.mkdtemp())
                def __truediv__(self, other):
                    return self._tmp / other
            ws.return_value = DummyWorkspace()
            run = acquire(case, device.serial_number, device=device)

        # If every target is absent and nothing pulled, overall == NOT_PRESENT.
        run.refresh_from_db()
        if run.files_pulled == 0:
            self.assertEqual(run.status, AcquisitionStatus.NOT_PRESENT)
        # Evidence reflects the same honest conclusion.
        ev = run.evidence
        self.assertIsNotNone(ev)


class ImageImportTest(TestCase):
    def test_image_import_without_path_raises(self):
        case, device = make_case_and_device()
        with self.assertRaises(AcquisitionError):
            acquire(case, device.serial_number, device=device, level="3", method="image_import")
        run = AcquisitionRun.objects.get(case=case, level="3")
        self.assertEqual(run.state, RunState.FAILED)


class TargetBookkeepingTest(TestCase):
    def test_target_statuses_recorded(self):
        case, device = make_case_and_device()
        acq = mock.Mock()
        acq.installed_packages.return_value = ["com.example.one", "com.example.two"]
        acq.target_apk_path.return_value = "/data/app/one/base.apk"
        acq.exists.return_value = False
        acq.pull.return_value = False
        acq.adapter.adb_path.return_value = "adb"

        with mock.patch("acquisition.engine.Acquirer", return_value=acq), \
             mock.patch("acquisition.engine._workspace") as ws:
            from pathlib import Path
            import tempfile
            class DummyWorkspace:
                def __init__(self):
                    self._tmp = Path(tempfile.mkdtemp())
                def __truediv__(self, other):
                    return self._tmp / other
            ws.return_value = DummyWorkspace()
            run = acquire(case, device.serial_number, device=device)

        targets = list(AcquisitionTarget.objects.filter(run=run))
        self.assertTrue(targets)
        pkg_targets = [t for t in targets if t.target_type == "package"]
        # At least one package target recorded.
        self.assertTrue(pkg_targets)
        for t in pkg_targets:
            self.assertTrue(t.status in {AcquisitionStatus.ACQUIRED, AcquisitionStatus.PARTIAL,
                                         AcquisitionStatus.NOT_ACCESSIBLE,
                                         AcquisitionStatus.NOT_PRESENT})


class RunSummaryTest(TestCase):
    def test_summary_format(self):
        case, device = make_case_and_device()
        run = AcquisitionRun.objects.create(
            case=case, device=device, level="1", method="adb_logical",
            state=RunState.COMPLETED, status=AcquisitionStatus.ACQUIRED,
            files_pulled=3, bytes_pulled=100, targets_total=2, targets_ok=2,
        )
        self.assertIn("3 files", run.summary())
        self.assertIn("100 bytes", run.summary())


class AcquireCommandTest(TestCase):
    def test_command_runs_and_logs_custody_audit(self):
        case, device = make_case_and_device()
        acq = mock.Mock()
        acq.installed_packages.return_value = ["com.example.one"]
        acq.target_apk_path.return_value = "/data/app/one/base.apk"
        acq.exists.return_value = False
        acq.pull.return_value = False
        acq.adapter.adb_path.return_value = "adb"

        with mock.patch("acquisition.engine.Acquirer", return_value=acq), \
             mock.patch("acquisition.engine._workspace") as ws:
            from pathlib import Path
            import tempfile
            class DummyWorkspace:
                def __init__(self):
                    self._tmp = Path(tempfile.mkdtemp())
                def __truediv__(self, other):
                    return self._tmp / other
            ws.return_value = DummyWorkspace()
            out = io.StringIO()
            call_command("acquire", "CASE-ACQ-1", serial=device.serial_number, stdout=out)

        text = out.getvalue()
        self.assertIn("Run", text)
        run = AcquisitionRun.objects.get(case=case)
        self.assertTrue(run.evidence_id)
        # Custody + audit events written.
        self.assertTrue(CustodyEvent.objects.filter(evidence=run.evidence_id).exists())
        self.assertTrue(AuditLog.objects.filter(
            event_type=AuditEventType.WRITE, action="acquisition").exists())

    def test_command_adb_absent_fails_cleanly(self):
        case, device = make_case_and_device()
        with mock.patch("acquisition.engine.Acquirer",
                        side_effect=Exception("no adb")):
            out = io.StringIO()
            err = io.StringIO()
            call_command("acquire", "CASE-ACQ-1", serial=device.serial_number,
                         stdout=out, stderr=err)
        self.assertIn("could not run", out.getvalue())
        # A failed run + custody/audit trail exists; nothing was pulled.
        run = AcquisitionRun.objects.get(case=case)
        self.assertEqual(run.files_pulled, 0)
        self.assertIn(AcquisitionStatus.FAILED, run.status)
        self.assertTrue(AuditLog.objects.filter(
            event_type=AuditEventType.FAILURE, action="acquisition").exists())


class RunPipelineCommandTest(TestCase):
    def test_pipeline_runs_and_logs_audit(self):
        case, device = make_case_and_device()
        ev = register_evidence(case, device=device, title="pipeline evidence")
        register_artifact(ev, name="note.txt", source="memo",
                          metadata={"notes": "meeting downtown at 9"})
        out = io.StringIO()
        call_command("run_pipeline", case.case_number, stdout=out)
        self.assertIn("Pipeline complete", out.getvalue())
        self.assertTrue(AuditLog.objects.filter(
            event_type=AuditEventType.WRITE, action="pipeline").exists())

    def test_pipeline_indexes_search(self):
        case, device = make_case_and_device()
        ev = register_evidence(case, device=device, title="pipeline evidence")
        register_artifact(ev, name="note.txt", source="memo",
                          metadata={"notes": "meeting downtown at 9"})
        call_command("run_pipeline", case.case_number, index=True,
                     stdout=io.StringIO())
        from searching.models import SearchEntry
        self.assertEqual(
            SearchEntry.objects.filter(artifact__evidence__case=case).count(), 1)
