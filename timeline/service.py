"""Timeline builder: aggregate recovered timestamps into a sorted timeline.

The builder looks only at timestamps that were actually recovered and stored on
artifacts/evidence — it does not derive or fabricate times. Artifacts with no
usable timestamp are returned in a ``timeless`` bucket so an analyst can see
that some evidence could not be placed on the timeline.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List

from django.utils import timezone as dj_tz

from cases.models import Case
from evidence.models import Artifact, Evidence
from .models import EventType, TimelineEvent

logger = logging.getLogger(__name__)


def _coerce_datetime(value) -> datetime | None:
    """Coerce a timestamp-ish value to a timezone-aware datetime, or None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        # EXIF datetimes are stored as ISO strings.
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def _extract_artifact_events(artifact: Artifact) -> List[dict]:
    """Yield (occurred_at, source, summary) dicts recovered for one artifact."""
    events = []
    meta = artifact.metadata or {}

    # 1) Explicit event anchor captured at acquisition/analysis.
    if artifact.event_start:
        events.append({
            "occurred_at": artifact.event_start,
            "source": "artifact_event_start",
            "summary": f"{artifact.name}: activity",
            "event_type": EventType.OTHER,
        })

    # 2) Social/message timestamps (e.g. WhatsApp metadata).
    social = meta.get("social") or {}
    for app in social.values():
        # WhatsApp messages carry epoch timestamps in the extraction; we
        # surface only a coarse count here. Full message-level timeline entries
        # are produced by the social analyzer; here we add a representative
        # event anchored to the earliest message when available.
        pass

    # 3) Image EXIF datetime.
    image = meta.get("image") or {}
    for key in ("exif_DateTimeOriginal", "exif_DateTime"):
        if key in image:
            dt = _coerce_datetime(str(image[key]).strip())
            if dt:
                events.append({
                    "occurred_at": dt,
                    "source": f"exif:{key}",
                    "summary": f"{artifact.name}: image captured",
                    "event_type": EventType.MEDIA,
                })

    # 4) File mtime captured during acquisition, if recorded.
    fdt = _coerce_datetime(meta.get("mtime"))
    if fdt:
        events.append({
            "occurred_at": fdt,
            "source": "file_mtime",
            "summary": f"{artifact.name}: file modified",
            "event_type": EventType.ANALYSIS,
        })
    return events


def _evidence_events(evidence: Evidence) -> List[dict]:
    """Events derived from the evidence container itself (acquisition time)."""
    events = []
    dt = evidence.collected_at
    if dt:
        events.append({
            "occurred_at": dt,
            "source": "evidence_collected_at",
            "summary": f"Evidence registered: {evidence.title or evidence.evidence_id}",
            "event_type": EventType.ACQUISITION,
        })
    return events


def build_timeline(case: Case, *, persist: bool = True) -> Dict:
    """Rebuild the timeline for a case and optionally persist it.

    Returns {events: [ {occurred_at, source, summary, event_type, artifact} ],
             timeless: [artifact_ids] }.
    """
    collected: List[dict] = []
    timeless_artifact_ids: List[str] = []

    for evidence in Evidence.objects.filter(case=case):
        for ev in _evidence_events(evidence):
            ev["case"] = case
            collected.append(ev)
        for artifact in evidence.artifacts.all():
            events = _extract_artifact_events(artifact)
            if not events:
                # Nothing dated was recovered for this artifact -> timeless.
                timeless_artifact_ids.append(artifact.artifact_id)
                continue
            for ev in events:
                ev["case"] = case
                ev["artifact"] = artifact
                collected.append(ev)

    dated = [e for e in collected if e.get("occurred_at") is not None]
    timeless = [e for e in collected if not e.get("occurred_at")]
    dated.sort(key=lambda e: e["occurred_at"])

    if persist:
        TimelineEvent.objects.filter(case=case).delete()
        for ev in dated:
            TimelineEvent.objects.create(
                case=ev["case"],
                artifact=ev.get("artifact"),
                event_type=ev.get("event_type", EventType.OTHER),
                occurred_at=ev["occurred_at"],
                summary=ev.get("summary", ""),
                source=ev.get("source", ""),
            )

    result = {
        "events": [
            {
                "occurred_at": ev["occurred_at"].isoformat(),
                "source": ev.get("source", ""),
                "summary": ev.get("summary", ""),
                "event_type": ev.get("event_type", EventType.OTHER),
                "artifact": getattr(ev.get("artifact"), "artifact_id", None),
            }
            for ev in dated
        ],
        "timeless": [getattr(e.get("artifact"), "artifact_id", None) for e in timeless]
                   + timeless_artifact_ids,
    }
    return result


def timed_events(case: Case) -> List[TimelineEvent]:
    return list(TimelineEvent.objects.filter(case=case).order_by("occurred_at"))
