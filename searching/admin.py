from django.contrib import admin

from .models import SearchEntry


@admin.register(SearchEntry)
class SearchEntryAdmin(admin.ModelAdmin):
    list_display = ("artifact", "case", "updated")
    search_fields = ("content", "artifact__artifact_id", "artifact__name")
    readonly_fields = ("updated",)
