"""DroidForenX device models.

The Device model records identification information about an Android device
that has been (or is being) examined. The `identification` field stores the
structured detail returned by the device detection layer so the raw and the
normalized representation are both preserved.
"""

from django.db import models


class Device(models.Model):
    """A physical Android device associated with a forensic case."""

    class Manufacturer(models.TextChoices):
        SAMSUNG = "samsung", "Samsung"
        GOOGLE = "google", "Google"
        XIAOMI = "xiaomi", "Xiaomi"
        REDMI = "redmi", "Redmi"
        OPPO = "oppo", "OPPO"
        ONEPLUS = "oneplus", "OnePlus"
        VIVO = "vivo", "Vivo"
        HUAWEI = "huawei", "Huawei"
        MOTOROLA = "motorola", "Motorola"
        NOKIA = "nokia", "Nokia"
        SONY = "sony", "Sony"
        ASUS = "asus", "ASUS"
        REALME = "realme", "Realme"
        TECNO = "tecno", "Tecno"
        INFINIX = "infinix", "Infinix"
        ZTE = "zte", "ZTE"
        LENOVO = "lenovo", "Lenovo"
        GENERIC = "generic", "Generic / Other Android"

    class AcquisitionStatus(models.TextChoices):
        NOT_ATTEMPTED = "not_attempted", "Not Attempted"
        AUTHORIZED = "authorized", "Authorized"
        NOT_AUTHORIZED = "not_authorized", "Not Authorized"
        FAILED = "failed", "Failed"

    class ConnectionState(models.TextChoices):
        DISCONNECTED = "disconnected", "Disconnected"
        CONNECTED = "connected", "Connected"
        UNAUTHORIZED = "unauthorized", "Unauthorized"
        OFFLINE = "offline", "Offline"
        UNKNOWN = "unknown", "Unknown"

    # Identification
    serial_number = models.CharField(max_length=128, unique=True)
    manufacturer = models.CharField(
        max_length=32, choices=Manufacturer.choices, default=Manufacturer.GENERIC
    )
    manufacturer_raw = models.CharField(
        max_length=128, blank=True, default="", help_text="Raw manufacturer string from device."
    )
    model = models.CharField(max_length=128, blank=True, default="")
    product = models.CharField(max_length=128, blank=True, default="")
    device_name = models.CharField(max_length=128, blank=True, default="")
    android_version = models.CharField(max_length=64, blank=True, default="")
    api_level = models.IntegerField(null=True, blank=True)
    build_number = models.CharField(max_length=128, blank=True, default="")
    codename = models.CharField(max_length=64, blank=True, default="")
    cpu_architecture = models.CharField(max_length=64, blank=True, default="")
    kernel_version = models.CharField(max_length=256, blank=True, default="")
    security_patch = models.CharField(
        max_length=32, blank=True, default="", help_text="e.g. 2026-08-05"
    )
    bootloader = models.CharField(max_length=64, blank=True, default="")

    # Flattened device properties (kept for fast queries; full map is in JSON below)
    ro_build_version_release = models.CharField(max_length=64, blank=True, default="")
    ro_product_model = models.CharField(max_length=128, blank=True, default="")

    # Security / authorisation state
    encryption_enabled = models.BooleanField(null=True, blank=True)
    usb_debugging_enabled = models.BooleanField(null=True, blank=True)
    authorized = models.BooleanField(null=True, blank=True)

    # Structured detection detail
    identification = models.JSONField(default=dict, blank=True)

    # Connection / acquisition state
    connection_state = models.CharField(
        max_length=32, choices=ConnectionState.choices, default=ConnectionState.UNKNOWN
    )
    acquisition_status = models.CharField(
        max_length=32, choices=AcquisitionStatus.choices, default=AcquisitionStatus.NOT_ATTEMPTED
    )

    # Audit
    first_seen = models.DateTimeField(auto_now_add=True)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_updated"]
        indexes = [
            models.Index(fields=["serial_number"]),
            models.Index(fields=["manufacturer"]),
            models.Index(fields=["api_level"]),
        ]
        verbose_name_plural = "devices"

    def __str__(self):
        return f"{self.manufacturer} {self.model} ({self.serial_number})"

    @property
    def display_name(self):
        """Human friendly name, e.g. 'Samsung SM-G991B (Android 12, API 31)'."""
        parts = [self.manufacturer_raw or self.get_manufacturer_display(), self.model]
        name = " ".join(p for p in parts if p) or self.serial_number
        if self.api_level:
            name += f" (Android {self.android_version}, API {self.api_level})"
        return name

    def capability_for(self, artifact_key):
        """Return the capability tier for an artifact on this device.

        Uses the compatibility layer with this device's API level/version.
        """
        from .android_compatibility import capability_for

        api = self.api_level
        if api is None and self.android_version:
            from .android_compatibility import api_from_version
            api = api_from_version(self.android_version)
        return capability_for(artifact_key, api)

    def compatibility_summary(self) -> dict:
        """Return the grouped supported/partial/unavailable artifact summary."""
        from .android_compatibility import api_from_version, summarize_report

        api = self.api_level
        if api is None and self.android_version:
            api = api_from_version(self.android_version)
        return summarize_report(api)

    def compatibility_report(self) -> dict:
        """Return a full plain-dict compatibility report for this device."""
        from .android_compatibility import compatibility_for_device

        return compatibility_for_device(self.api_level, self.android_version)
