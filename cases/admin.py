from django.contrib import admin

from .models import Case, Investigator


@admin.register(Case)
class CaseAdmin(admin.ModelAdmin):
    list_display = ("case_number", "title", "status", "lead_investigator", "opened")
    list_filter = ("status",)
    search_fields = ("case_number", "title")
    filter_horizontal = ("investigators", "devices")
    readonly_fields = ("opened", "updated")


@admin.register(Investigator)
class InvestigatorAdmin(admin.ModelAdmin):
    list_display = ("name", "badge_id", "organization", "rank")
    search_fields = ("name", "badge_id", "organization")