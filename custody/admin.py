from django.contrib import admin

from .models import CustodyEvent


@admin.register(CustodyEvent)
class CustodyEventAdmin(admin.ModelAdmin):
    list_display = ("evidence", "action", "actor", "occurred_at", "description")
    list_filter = ("action",)
    search_fields = ("evidence__evidence_id", "description", "actor__username")
    readonly_fields = ("occurred_at",)

    def has_change_permission(self, request, obj=None):
        # Chain-of-custody is append-only.
        return False

    def has_delete_permission(self, request, obj=None):
        return False
