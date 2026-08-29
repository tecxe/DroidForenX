"""Timeline models.

A unified, sortable timeline of what the platform actually recovered for a
case. ``TimelineEvent`` rows are derived from the evidence artifacts (message
timestamps, EXIF datetimes, file modification times, acquisition events). Every
event is anchored to a real timestamp that was extracted — the builder never
invents a time, and events lacking a usable timestamp are excluded from the
timeline and reported separately as ``timeless``.
"""

from django.db import models


class EventType(models.TextChoices):
    ACQUISITION = "acquisition", "Acquisition"
    MESSAGE = "message", "Message"
    MEDIA = "media", "Media File"
    DATABASE = "database", "Database Activity"
    RECOVERY = "recovery", "Recovery/Carve"
    ANALYSIS = "analysis", "Analysis Datapoint"
    OTHER = "other", "Other"


class TimelineEvent(models.Model):
    id = models.BigAutoField(primary_key=True)
    case = models.ForeignKey("cases.Case", on_delete=models.CASCADE, related_name="timeline")
    artifact = models.ForeignKey(
        "evidence.Artifact", on_delete=models.SET_NULL, null=True, blank=True, related_name="timeline_events"
    )
    event_type = models.CharField(max_length=24, choices=EventType.choices, default=EventType.OTHER)
    occurred_at = models.DateTimeField()
    occurred_end = models.DateTimeField(null=True, blank=True)
    summary = models.CharField(max_length=512)
    detail = models.JSONField(default=dict, blank=True)

    # Confidence in the timestamp's provenance (e.g. EXIF vs file mtime).
    source = models.CharField(max_length=64, blank=True, default="")

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["occurred_at"]
        indexes = [
            models.Index(fields=["case", "occurred_at"]),
            models.Index(fields=["case", "event_type"]),
        ]

    def __str__(self):
        return f"[{self.occurred_at.isoformat()}] {self.event_type}: {self.summary}"
