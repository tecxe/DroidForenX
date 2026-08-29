from django.contrib import admin

from .models import AcquisitionRun, AcquisitionTarget


@admin.register(AcquisitionRun)
class AcquisitionRunAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "case",
        "device",
        "level",
        "state",
        "status",
        "files_pulled",
        "bytes_pulled",
        "created",
    )
    list_filter = ("level", "state", "status", "method")
    readonly_fields = ("id", "created", "updated")
    search_fields = ("case__case_number", "device__serial_number", "notes")


@admin.register(AcquisitionTarget)
class AcquisitionTargetAdmin(admin.ModelAdmin):
    list_display = ("target_type", "name", "run", "status", "files_pulled", "bytes_pulled")
    list_filter = ("target_type", "status")
    search_fields = ("name",)
    readonly_fields = ("created",)
