"""Admin interfaces for electrical circuits and network drops."""

from django.contrib import admin
from django.urls import path

from .admin_reports import (
    breaker_trip_impact_view,
    circuit_load_report_view,
    shared_circuit_audit_view,
)
from .models import (
    Breaker,
    Disconnect,
    LightSwitch,
    NetworkDrop,
    Outlet,
    PowerBreaker,
    PowerCircuit,
    PowerOutlet,
    PowerPanel,
)


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


class PowerBreakerInline(admin.TabularInline):
    model = PowerBreaker
    extra = 0
    fields = ["position", "pole_count", "amperage", "phase", "status", "label", "needs_review"]
    show_change_link = True


class PowerCircuitInline(admin.TabularInline):
    model = PowerCircuit
    extra = 0
    fields = ["label", "conductor_size", "conductor_length_ft", "max_load_amps", "needs_review"]
    show_change_link = True


class PowerOutletInline(admin.TabularInline):
    model = PowerOutlet
    extra = 0
    fields = ["label", "outlet_type", "location", "status", "needs_review"]
    autocomplete_fields = ["location"]
    show_change_link = True


@admin.register(PowerPanel)
class PowerPanelAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "location",
        "phase_configuration",
        "voltage",
        "main_breaker_amperage",
        "needs_review",
    ]
    list_filter = ["phase_configuration", "voltage", "needs_review", "location"]
    search_fields = ["name", "manufacturer", "model", "location__name"]
    autocomplete_fields = ["location"]
    inlines = [PowerBreakerInline]


@admin.register(PowerBreaker)
class PowerBreakerAdmin(admin.ModelAdmin):
    list_display = [
        "panel",
        "position",
        "pole_count",
        "amperage",
        "phase",
        "status",
        "label",
        "needs_review",
    ]
    list_filter = ["status", "pole_count", "phase", "needs_review", "panel"]
    search_fields = ["panel__name", "position", "label", "panel__location__name"]
    autocomplete_fields = ["panel"]
    filter_horizontal = ["required_loto_devices"]
    inlines = [PowerCircuitInline]

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "trip-impact/",
                self.admin_site.admin_view(lambda request: breaker_trip_impact_view(self, request)),
                name="electrical_circuits_powerbreaker_trip_impact",
            ),
        ]
        return custom + urls


@admin.register(PowerCircuit)
class PowerCircuitAdmin(admin.ModelAdmin):
    list_display = [
        "label",
        "breaker",
        "conductor_size",
        "conductor_length_ft",
        "max_load_amps",
        "needs_review",
    ]
    list_filter = ["needs_review", "breaker__panel"]
    search_fields = ["label", "breaker__panel__name", "breaker__position"]
    autocomplete_fields = ["breaker"]
    inlines = [PowerOutletInline]

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "load-report/",
                self.admin_site.admin_view(lambda request: circuit_load_report_view(self, request)),
                name="electrical_circuits_powercircuit_load_report",
            ),
            path(
                "shared-circuit-audit/",
                self.admin_site.admin_view(
                    lambda request: shared_circuit_audit_view(self, request)
                ),
                name="electrical_circuits_powercircuit_shared_audit",
            ),
        ]
        return custom + urls


@admin.register(PowerOutlet)
class PowerOutletAdmin(admin.ModelAdmin):
    list_display = [
        "label",
        "outlet_type",
        "circuit",
        "location",
        "disconnect",
        "status",
        "needs_review",
    ]
    list_filter = ["outlet_type", "status", "needs_review", "location"]
    search_fields = ["label", "location_description", "location__name"]
    autocomplete_fields = ["circuit", "location", "legacy_outlet"]
    raw_id_fields = ["disconnect"]


@admin.register(Disconnect)
class DisconnectAdmin(admin.ModelAdmin):
    list_display = [
        "label",
        "circuit",
        "location",
        "disconnect_type",
        "amperage",
        "is_lockable",
        "needs_review",
    ]
    list_filter = ["disconnect_type", "is_lockable", "needs_review", "location"]
    search_fields = ["label", "notes", "location__name"]
    autocomplete_fields = ["circuit", "location"]
    filter_horizontal = ["required_loto_devices"]
