from django.contrib import admin

from .models import TimelineEvent


@admin.register(TimelineEvent)
class TimelineEventAdmin(admin.ModelAdmin):
    list_display = ("case", "occurred_at", "event_type", "summary", "source")
    list_filter = ("event_type", "source")
    search_fields = ("summary", "case__case_number")
    readonly_fields = ("created",)
