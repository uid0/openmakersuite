"""Admin interfaces for electrical circuits and network drops."""

from django.contrib import admin

from .models import Breaker, LightSwitch, NetworkDrop, Outlet


@admin.register(Breaker)
class BreakerAdmin(admin.ModelAdmin):
    list_display = [
        "panel",
        "breaker_number",
        "amperage",
        "voltage",
        "poles",
        "location",
        "is_active",
    ]
    list_filter = ["panel", "voltage", "poles", "is_active", "location"]
    search_fields = ["panel", "breaker_number", "description", "location__name"]


@admin.register(Outlet)
class OutletAdmin(admin.ModelAdmin):
    list_display = ["identifier", "location", "outlet_type", "breaker", "is_active"]
    list_filter = ["outlet_type", "is_active", "location"]
    search_fields = [
        "identifier",
        "description",
        "plugged_in_notes",
        "location__name",
    ]
    autocomplete_fields = ["breaker", "location"]


@admin.register(LightSwitch)
class LightSwitchAdmin(admin.ModelAdmin):
    list_display = [
        "identifier",
        "location",
        "controls_location",
        "breaker",
        "is_active",
    ]
    list_filter = ["is_active", "location"]
    search_fields = ["identifier", "description", "notes", "location__name"]
    autocomplete_fields = ["breaker", "location", "controls_location"]


@admin.register(NetworkDrop)
class NetworkDropAdmin(admin.ModelAdmin):
    list_display = [
        "identifier",
        "location",
        "drop_type",
        "patch_panel",
        "patch_port",
        "is_active",
    ]
    list_filter = ["drop_type", "is_active", "location"]
    search_fields = [
        "identifier",
        "description",
        "notes",
        "patch_panel",
        "mac_address",
        "location__name",
    ]
    autocomplete_fields = ["location"]
