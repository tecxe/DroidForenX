"""Models for authorized acquisition runs.

An ``AcquisitionRun`` records the act of acquiring data from a device along a
chosen acquisition level. It is always tied to an ``Evidence`` container that
logs the outcome in a single, auditable chain. Because the platform only
reports what it actually recovered, a run keeps an explicit accounting of
targets (packages or paths) with a per-target status and pull counts, and
records when ADB itself is unavailable so operators are never misled.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from django.conf import settings
from django.db import models

from cases.models import Case
from devices.models import Device
from evidence.models import AcquisitionStatus, Evidence

AVAILABLE_STATUSES = {
    AcquisitionStatus.ACQUIRED,
    AcquisitionStatus.PARTIAL,
    AcquisitionStatus.FAILED,
    AcquisitionStatus.NOT_ACCESSIBLE,
    AcquisitionStatus.NOT_PRESENT,
}


class AcquisitionLevel(models.TextChoices):
    """Implements a defined acquisition-level model.

    Level 1 is a logical (user-space) extraction: only files that the debug
    interface makes readable. Level 3 is a physical-style image (or an
    investigator-supplied image import). Level 2 is a file-system-level pull
    of readable storage with no physical block access.
    """

    L1_LOGICAL = "1", "Level 1 - Logical"
    L2_FILESYSTEM = "2", "Level 2 - File-system"
    L3_PHYSICAL = "3", "Level 3 - Physical/Image"


class RunState(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class AcquisitionRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="acquisition_runs")
    device = models.ForeignKey(
        Device, on_delete=models.PROTECT, related_name="acquisition_runs", null=True, blank=True
    )
    evidence = models.ForeignKey(
        Evidence, on_delete=models.CASCADE, related_name="acquisition_runs", null=True, blank=True
    )
    level = models.CharField(max_length=1, choices=AcquisitionLevel.choices, default=AcquisitionLevel.L1_LOGICAL)
    method = models.CharField(max_length=64, default="adb_logical")
    state = models.CharField(max_length=16, choices=RunState.choices, default=RunState.PENDING)
    status = models.CharField(
        max_length=40, choices=AcquisitionStatus.choices, default=AcquisitionStatus.ACQUIRED
    )
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="acquisition_runs",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    notes = models.TextField(blank=True)
    # Auditable accounting (honest system): counts are filled in from actual results.
    targets_total = models.IntegerField(default=0)
    targets_ok = models.IntegerField(default=0)
    targets_failed = models.IntegerField(default=0)
    files_pulled = models.IntegerField(default=0)
    bytes_pulled = models.BigIntegerField(default=0)

    class Meta:
        ordering = ("-created",)
        indexes = [
            models.Index(fields=["case", "state"]),
            models.Index(fields=["device", "level"]),
        ]

    def __str__(self):
        return f"Acq {self.id} · {self.get_level_display()} · {self.state}"

    def summary(self) -> str:
        return (
            f"{self.get_status_display()}: {self.files_pulled} files, "
            f"{self.bytes_pulled} bytes across {self.targets_total} targets"
            f" ({self.targets_ok} ok / {self.targets_failed} failed)"
        )

    def mark_started(self, when=None):
        from django.utils import timezone
        self.state = RunState.RUNNING
        self.started_at = when or timezone.now()
        self.save(update_fields=["state", "started_at", "updated"])

    def finalize(self, status: str, when=None, save: bool = True):
        from django.utils import timezone
        self.state = RunState.COMPLETED if status != AcquisitionStatus.FAILED else RunState.FAILED
        self.status = status
        self.finished_at = when or timezone.now()
        if save:
            self.save(update_fields=["state", "status", "finished_at", "updated"])


class AcquisitionTarget(models.Model):
    """One logical acquisition target (an app package or a path on device)."""

    run = models.ForeignKey(AcquisitionRun, on_delete=models.CASCADE, related_name="targets")
    target_type = models.CharField(
        max_length=16,
        choices=[("package", "Package"), ("path", "Path"), ("provider", "Content provider")],
    )
    name = models.CharField(max_length=512)
    detail = models.TextField(blank=True)
    status = models.CharField(
        max_length=40, choices=AcquisitionStatus.choices, default=AcquisitionStatus.ACQUIRED
    )
    files_pulled = models.IntegerField(default=0)
    bytes_pulled = models.BigIntegerField(default=0)
    message = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created",)
        indexes = [models.Index(fields=["run", "status"])]

    def __str__(self):
        return f"{self.target_type} {self.name} · {self.get_status_display()}"
