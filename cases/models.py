"""Case and Investigator models.

Every evidence item in the platform belongs to a forensic case. The Case
naturally references a primary investigator; additional investigator records
store who performed collection/analysis actions.
"""

from django.conf import settings
from django.db import models


class Investigator(models.Model):
    """A forensic investigator / analyst who operates on evidence."""

    class Rank(models.TextChoices):
        ANALYST = "analyst", "Analyst"
        EXAMINER = "examiner", "Examiner"
        LEAD = "lead", "Lead Investigator"
        ADMIN = "admin", "Administrator"

    name = models.CharField(max_length=255)
    badge_id = models.CharField(max_length=128, blank=True, default="")
    organization = models.CharField(max_length=255, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    rank = models.CharField(max_length=32, choices=Rank.choices, default=Rank.ANALYST)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="investigator_profile",
    )
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name or self.badge_id or f"Investigator {self.pk}"


class Case(models.Model):
    """A forensic case containing devices, evidence, and analyses."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"
        ARCHIVED = "archived", "Archived"
        SEALED = "sealed", "Sealed"

    case_number = models.CharField(max_length=64, unique=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)

    lead_investigator = models.ForeignKey(
        Investigator,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cases",
    )
    investigators = models.ManyToManyField(
        Investigator,
        blank=True,
        related_name="assigned_cases",
    )
    devices = models.ManyToManyField("devices.Device", blank=True, related_name="cases")

    opened = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-opened"]
        indexes = [
            models.Index(fields=["case_number"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.case_number} - {self.title}"

    def evidence_count(self):
        from evidence.models import Evidence
        return Evidence.objects.filter(case=self).count()

    def device_count(self):
        return self.devices.count()