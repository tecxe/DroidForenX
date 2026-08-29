from django.contrib import admin

from .models import Device


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = (
        "serial_number",
        "manufacturer",
        "model",
        "android_version",
        "api_level",
        "connection_state",
        "last_updated",
    )
    list_filter = ("manufacturer", "connection_state", "api_level")
    search_fields = ("serial_number", "model", "manufacturer_raw")
    readonly_fields = ("first_seen", "last_updated", "identification")
    fieldsets = (
        (None, {"fields": ("serial_number", "manufacturer", "manufacturer_raw", "model")}),
        ("System", {"fields": ("android_version", "api_level", "build_number", "codename")}),
        ("Hardware", {"fields": ("product", "device_name", "cpu_architecture", "kernel_version")}),
        ("Security", {"fields": ("security_patch", "bootloader", "encryption_enabled",
                                 "usb_debugging_enabled", "authorized")}),
        ("Detection detail", {"fields": ("identification",)}),
        ("State", {"fields": ("connection_state", "acquisition_status")}),
        ("Timestamps", {"fields": ("first_seen", "last_updated")}),
    )
