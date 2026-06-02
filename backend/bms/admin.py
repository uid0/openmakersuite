"""
Admin views for BMS configs + thermostat bindings.

BmsConfig admin never surfaces plaintext tokens (they're encrypted at
rest with Fernet; we don't decrypt to display in a browser). It does
surface expiry, last-sync, and the most recent sync error so an
operator can tell at a glance whether the integration is working.

A custom "Discover thermostats" action calls adapter.list_thermostats()
and renders the candidates as a flat list with the device_id /
location_id pre-filled; clicking one creates a ThermostatBinding for
an OMS-side Thermostat the operator picks.
"""

from __future__ import annotations

import logging

from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.html import format_html

from .adapters import BmsAdapterError, adapter_for
from .models import BmsConfig, ThermostatBinding

logger = logging.getLogger(__name__)


@admin.register(BmsConfig)
class BmsConfigAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "adapter_type",
        "is_active",
        "access_token_expires_at",
        "last_synced_at",
        "sync_health",
    ]
    list_filter = ["adapter_type", "is_active"]
    search_fields = ["name"]
    readonly_fields = [
        "id",
        "encrypted_access_token_repr",
        "encrypted_refresh_token_repr",
        "access_token_expires_at",
        "last_synced_at",
        "last_sync_error",
        "created_at",
        "updated_at",
        "discover_link",
    ]
    fields = [
        "id",
        "name",
        "adapter_type",
        "is_active",
        "encrypted_access_token_repr",
        "encrypted_refresh_token_repr",
        "access_token_expires_at",
        "last_synced_at",
        "last_sync_error",
        "discover_link",
        "created_at",
        "updated_at",
    ]

    @admin.display(description="Access token (encrypted)")
    def encrypted_access_token_repr(self, obj):
        return _token_summary(obj.encrypted_access_token)

    @admin.display(description="Refresh token (encrypted)")
    def encrypted_refresh_token_repr(self, obj):
        return _token_summary(obj.encrypted_refresh_token)

    @admin.display(description="Sync health")
    def sync_health(self, obj):
        if not obj.last_synced_at:
            return "never"
        if obj.last_sync_error:
            return format_html('<span style="color:#b00">errors</span>')
        return "ok"

    @admin.display(description="Discover thermostats")
    def discover_link(self, obj):
        if not obj.pk:
            return "—"
        url = reverse("admin:bms_bmsconfig_discover", args=[obj.pk])
        return format_html('<a href="{}">Discover thermostats on this BMS</a>', url)

    def get_urls(self):
        custom = [
            path(
                "<uuid:pk>/discover/",
                self.admin_site.admin_view(self.discover_view),
                name="bms_bmsconfig_discover",
            ),
        ]
        return custom + super().get_urls()

    def discover_view(self, request, pk):
        config = self.get_object(request, pk)
        if config is None:
            self.message_user(request, "Config not found.", messages.ERROR)
            return redirect("admin:bms_bmsconfig_changelist")
        try:
            adapter = adapter_for(config)
            candidates = adapter.list_thermostats()
        except BmsAdapterError as exc:
            self.message_user(
                request,
                f"Discovery failed: {exc}",
                messages.ERROR,
            )
            return redirect(reverse("admin:bms_bmsconfig_change", args=[config.pk]))

        if not candidates:
            self.message_user(
                request,
                "Adapter returned no thermostats. Check the account scope on " "the BMS side.",
                messages.WARNING,
            )
        else:
            existing = set(
                ThermostatBinding.objects.filter(config=config).values_list(
                    "external_device_id", flat=True
                )
            )
            lines = []
            for c in candidates:
                marker = "✓" if c.device_id in existing else "+"
                lines.append(
                    f"{marker} {c.device_id} (loc={c.location_id}) " f"{c.name!r} {c.model!r}"
                )
            self.message_user(
                request,
                format_html(
                    "Found {n} thermostat(s) — already-bound marked with ✓:"
                    "<br><pre style='white-space:pre-wrap'>{lines}</pre>"
                    "<br>Use the ThermostatBinding admin to attach an "
                    "unbound device to an OMS Thermostat row.",
                    n=len(candidates),
                    lines="\n".join(lines),
                ),
                messages.INFO,
            )
        return redirect(reverse("admin:bms_bmsconfig_change", args=[config.pk]))

    # Tokens are managed by the bms_resideo_auth command; the admin
    # is for inspection + status only.
    def has_add_permission(self, request):
        # Allow the row to be created (so the admin can flip is_active off
        # before re-auth), but operators should normally use the management
        # command which also walks the OAuth dance.
        return request.user.is_active and request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        # Bindings PROTECT this row; deleting a config requires deleting
        # the bindings first, which the operator does deliberately.
        return request.user.is_active and request.user.is_superuser


@admin.register(ThermostatBinding)
class ThermostatBindingAdmin(admin.ModelAdmin):
    list_display = [
        "thermostat",
        "config",
        "external_device_id",
        "indoor_temp_f",
        "cool_setpoint_f",
        "heat_setpoint_f",
        "hvac_mode",
        "last_synced_at",
        "binding_health",
    ]
    list_filter = ["config", "hvac_mode"]
    search_fields = [
        "external_device_id",
        "thermostat__location__name",
        "config__name",
    ]
    raw_id_fields = ["thermostat", "config"]
    readonly_fields = [
        "id",
        "indoor_temp_f",
        "indoor_humidity_pct",
        "cool_setpoint_f",
        "heat_setpoint_f",
        "hvac_mode",
        "fan_mode",
        "state_raw",
        "last_synced_at",
        "last_sync_error",
        "created_at",
        "updated_at",
    ]

    @admin.display(description="Sync health")
    def binding_health(self, obj):
        if not obj.last_synced_at:
            return "never"
        if obj.last_sync_error:
            return format_html('<span style="color:#b00">err</span>')
        return "ok"


def _token_summary(blob) -> str:
    blob = bytes(blob or b"")
    if not blob:
        return "(none — run `bms_resideo_auth`)"
    return f"present ({len(blob)} bytes ciphertext)"
