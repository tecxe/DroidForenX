from django.contrib import admin

from .models import HashRecord


@admin.register(HashRecord)
class HashRecordAdmin(admin.ModelAdmin):
    list_display = ("artifact", "algorithm", "digest", "verified_at", "verified_ok")
    list_filter = ("algorithm", "verified_ok")
    search_fields = ("digest", "artifact__artifact_id", "artifact__name")
    readonly_fields = ("created",)
