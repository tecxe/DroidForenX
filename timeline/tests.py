"""Tests for the forensic timeline builder."""

from datetime import datetime, timezone

from django.test import TestCase
from django.utils import timezone as dj_tz

from cases.models import Case, Investigator
from devices.models import Device
from evidence.services import register_artifact, register_evidence

from .models import EventType, TimelineEvent
from .service import build_timeline, timed_events


def make_case():
    inv = Investigator.objects.create(name="Ada")
    return Case.objects.create(case_number="CASE-TL-1", lead_investigator=inv)


def dt(y, m, d, hh=0, mm=0, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=timezone.utc)


class TimelineBuildTest(TestCase):
    def test_aggregates_evidence_and_artifact_events(self):
        case = make_case()
        ev = register_evidence(case, title="container")
        # Artifact anchored at a specific time via event_start.
        a = register_artifact(ev, name="msg", source="s", event_start=dt(2024, 1, 5, 10, 0))
        # Artifact with EXIF datetime in metadata.
        b = register_artifact(ev, name="photo.jpg", source="s",
                              metadata={"image": {"exif_DateTimeOriginal": "2024-01-05T09:00:00"}})

        result = build_timeline(case, persist=True)

        summary_sources = {e["source"]: e["occurred_at"] for e in result["events"]}
        self.assertIn("evidence_collected_at", summary_sources)
        self.assertIn("artifact_event_start", summary_sources)
        self.assertIn("exif:exif_DateTimeOriginal", summary_sources)

        # Sorted chronological order.
        times = [e["occurred_at"] for e in result["events"]]
        self.assertEqual(times, sorted(times))

        # Persisted.
        self.assertGreaterEqual(TimelineEvent.objects.filter(case=case).count(), 3)

    def test_exif_datetime_is_tz_aware(self):
        case = make_case()
        ev = register_evidence(case, title="c")
        register_artifact(ev, name="p.jpg", source="s",
                          metadata={"image": {"exif_DateTime": "2024-03-01T12:30:00"}})
        result = build_timeline(case, persist=False)
        evt = next(e for e in result["events"] if e["source"].startswith("exif"))
        self.assertTrue(evt["occurred_at"].endswith("+00:00"))

    def test_timeless_artifacts_bucketed(self):
        case = make_case()
        ev = register_evidence(case, title="c")
        a = register_artifact(ev, name="no-time", source="s", metadata={})
        result = build_timeline(case, persist=False)
        # 'a' has no timestamp -> in timeless bucket.
        self.assertIn(a.artifact_id, result["timeless"])

    def test_rebuild_replaces_prior(self):
        case = make_case()
        ev = register_evidence(case, title="c")
        register_artifact(ev, name="x", source="s", event_start=dt(2024, 1, 1))
        build_timeline(case, persist=True)
        build_timeline(case, persist=True)  # second build should not duplicate
        self.assertEqual(TimelineEvent.objects.filter(case=case).count(),
                         len(timed_events(case)))

    def test_device_evidence_event_type_acquisition(self):
        case = make_case()
        dev = Device.objects.create(serial_number="S1", manufacturer="google", model="P")
        ev = register_evidence(case, device=dev, title="cap")
        result = build_timeline(case, persist=True)
        acq = [e for e in result["events"] if e["event_type"] == EventType.ACQUISITION]
        self.assertTrue(acq)
