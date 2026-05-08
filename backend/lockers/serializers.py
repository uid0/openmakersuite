"""DRF serializers for the Lockers app webhook + admin surfaces."""

from __future__ import annotations

from rest_framework import serializers

from .models import Locker, LockerDevice, LockerOtp


class LockerSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source="location.name", read_only=True)
    owning_sig_name = serializers.CharField(source="owning_sig.name", read_only=True)
    current_asset_name = serializers.CharField(
        source="current_asset.name", read_only=True, default=None, allow_null=True
    )

    class Meta:
        model = Locker
        fields = (
            "id",
            "name",
            "slug",
            "location",
            "location_name",
            "owning_sig",
            "owning_sig_name",
            "description",
            "power_source",
            "current_asset",
            "current_asset_name",
            "is_high_trust",
            "led_count",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")


class LockerOtpSerializer(serializers.ModelSerializer):
    locker_name = serializers.CharField(source="locker.name", read_only=True)
    state = serializers.CharField(read_only=True)

    class Meta:
        model = LockerOtp
        fields = (
            "id",
            "locker",
            "locker_name",
            "requesting_user",
            "code",
            "expires_at",
            "used_at",
            "revoked_at",
            "revoked_by",
            "created_at",
            "state",
        )
        read_only_fields = (
            "id",
            "code",
            "expires_at",
            "used_at",
            "revoked_at",
            "revoked_by",
            "created_at",
            "state",
        )


class LockerDeviceSerializer(serializers.ModelSerializer):
    device_mac = serializers.CharField(source="device.mac_address", read_only=True)

    class Meta:
        model = LockerDevice
        fields = ("id", "locker", "device", "device_mac", "role", "is_primary", "notes")
        read_only_fields = ("id",)


# ---------------------------------------------------------------------------
# Webhook payloads (EMQX rule-engine forwards)
# ---------------------------------------------------------------------------


class _WebhookEventBase(serializers.Serializer):
    """Common envelope: every EMQX rule-engine forward carries a MAC and a
    timestamp from the broker. Subclasses add event-specific fields.
    """

    mac = serializers.CharField(max_length=17)
    timestamp = serializers.DateTimeField(required=False, allow_null=True)


class LockoutEventSerializer(_WebhookEventBase):
    """Forwarded when firmware enters or exits a fail-secure lockout
    (e.g. tamper, repeated auth failure)."""

    state = serializers.ChoiceField(choices=("entered", "cleared"))
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class IrBreakEventSerializer(_WebhookEventBase):
    """Forwarded when the IR-break beam crosses inside an open locker —
    Phase 4 will correlate with reed-switch state to flag
    "Item Removed" events."""

    broken = serializers.BooleanField()


class ReedStatusEventSerializer(_WebhookEventBase):
    """Forwarded on reed-switch state change (door open/closed). Phase 4
    uses this for unlock-to-secure duration tracking."""

    closed = serializers.BooleanField()
