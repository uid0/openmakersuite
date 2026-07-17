"""Read-only staff admin for the OMS ledger side-tables.

The ledger is service-managed and append-only, so these admins are for staff
debugging visibility only — add/change/delete are all disabled. hordak
self-registers its own ``Account`` / ``Transaction`` / ``Leg`` admins.
"""

from django.contrib import admin

from .models import EntryMeta, LegDimension


class _ReadOnlyModelAdmin(admin.ModelAdmin):
    """A ``ModelAdmin`` that permits viewing but never mutation."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EntryMeta)
class EntryMetaAdmin(_ReadOnlyModelAdmin):
    list_display = (
        "id",
        "transaction",
        "source_type",
        "source_ref",
        "created_by",
        "posted_at",
    )
    list_filter = ("source_type", "posted_at")
    search_fields = ("source_ref", "transaction__uuid")
    raw_id_fields = ("transaction", "reverses", "created_by")
    date_hierarchy = "posted_at"


@admin.register(LegDimension)
class LegDimensionAdmin(_ReadOnlyModelAdmin):
    list_display = ("id", "leg", "sig", "asset")
    list_filter = ("sig",)
    search_fields = ("leg__uuid", "sig__name", "asset__name")
    raw_id_fields = ("leg", "sig", "asset")
