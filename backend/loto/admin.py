"""Admin interfaces for LOTO devices and asset energy sources."""

from django.contrib import admin

from .models import AssetEnergySource, LOTODevice


@admin.register(LOTODevice)
class LOTODeviceAdmin(admin.ModelAdmin):
    list_display = [
        "label",
        "device_type",
        "status",
        "location",
        "assigned_to",
        "updated_at",
    ]
    list_filter = ["device_type", "status", "location"]
    search_fields = ["label", "notes", "location__name", "assigned_to__username"]
    autocomplete_fields = ["location", "assigned_to"]


@admin.register(AssetEnergySource)
class AssetEnergySourceAdmin(admin.ModelAdmin):
    list_display = ["asset", "source_type", "magnitude", "isolation_point"]
    list_filter = ["source_type"]
    search_fields = [
        "asset__name",
        "asset__asset_tag",
        "isolation_point",
        "magnitude",
        "notes",
    ]
    autocomplete_fields = ["asset"]
    filter_horizontal = ["required_devices"]
