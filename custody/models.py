"""Chain-of-custody models.

A continuous, append-only record of who handled each evidence item, when, and
what they did with it. Custody events are written once and never edited or
deleted from the application layer; the trail is the guarantee of integrity.
"""

from django.conf import settings
from django.db import models


class CustodyAction(models.TextChoices):
    ACQUIRED = "acquired", "Acquired"
    RECEIVED = "received", "Received"
    HASHED = "hashed", "Hashed / Verified"
    ANALYZED = "analyzed", "Analyzed"
    ACCESSED = "accessed", "Viewed / Accessed"
    EXPORTED = "exported", "Exported / Shared"
    TRANSFERRED = "transferred", "Transferred Custody"
    RETURNED = "returned", "Returned to Owner"
    STORED = "stored", "Stored"


class CustodyEvent(models.Model):
    id = models.BigAutoField(primary_key=True)
    evidence = models.ForeignKey(
        "evidence.Evidence", on_delete=models.CASCADE, related_name="custody_events"
    )
    action = models.CharField(max_length=24, choices=CustodyAction.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="custody_events",
    )
    investigator = models.ForeignKey(
        "cases.Investigator", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="custody_events",
    )
    description = models.CharField(max_length=512, blank=True, default="")
    note = models.TextField(blank=True, default="")
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["occurred_at"]
        indexes = [
            models.Index(fields=["evidence", "occurred_at"]),
        ]

    def __str__(self):
        return f"{self.occurred_at.isoformat()} {self.action} {self.evidence.evidence_id}"

    # Append-only protection: forbid updates and deletes from application code.
    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise NotImplementedError("Custody events are append-only and cannot be modified.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise NotImplementedError("Custody events are append-only and cannot be deleted.")
