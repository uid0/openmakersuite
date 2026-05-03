"""Admin registrations for the devices app.

The :class:`InterfaceInline` is appended to the existing
:class:`inventory.admin.AssetAdmin` so an Asset detail page shows its
interfaces inline (AC-3).
"""

from __future__ import annotations

from django.contrib import admin

from inventory.admin import AssetAdmin

from .models import Interface, NetworkDrop


class InterfaceInline(admin.TabularInline):
    model = Interface
    extra = 0
    fields = (
        "name",
        "interface_type",
        "mac_address",
        "mtu",
        "enabled",
        "description",
    )


# Append our inline rather than re-registering AssetAdmin, so other apps can
# also add inlines without stomping each other.
AssetAdmin.inlines = list(AssetAdmin.inlines) + [InterfaceInline]


@admin.register(Interface)
class InterfaceAdmin(admin.ModelAdmin):
    list_display = (
        "asset",
        "name",
        "interface_type",
        "mac_address",
        "enabled",
    )
    list_filter = ("interface_type", "enabled")
    search_fields = (
        "asset__name",
        "asset__asset_tag",
        "name",
        "mac_address",
        "description",
    )
    autocomplete_fields = ("asset",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(NetworkDrop)
class NetworkDropAdmin(admin.ModelAdmin):
    list_display = (
        "drop_label",
        "location",
        "wire_type",
        "patch_panel_position",
    )
    list_filter = ("wire_type", "location")
    search_fields = (
        "drop_label",
        "location__name",
        "patch_panel_position",
        "notes",
    )
    autocomplete_fields = ("location",)
    readonly_fields = ("created_at", "updated_at")
