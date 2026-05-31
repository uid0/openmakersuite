"""DRF serializers for the Lockers app webhook + admin surfaces."""

from __future__ import annotations

from django.utils.text import slugify

from rest_framework import serializers

from .models import Locker, LockerDevice, LockerOtp, LockerStatus


class LockerSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source="location.name", read_only=True)
    owning_sig_name = serializers.CharField(source="owning_sig.name", read_only=True)
    current_asset_name = serializers.CharField(
        source="current_asset.name", read_only=True, default=None, allow_null=True
    )
    required_certification_names = serializers.SerializerMethodField()

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
            "required_certifications",
            "required_certification_names",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def get_required_certification_names(self, obj) -> list[str]:
        return [c.name for c in obj.required_certifications.all()]


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
    device_is_online = serializers.BooleanField(source="device.is_online", read_only=True)
    role_display = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = LockerDevice
        fields = (
            "id",
            "locker",
            "device",
            "device_mac",
            "device_is_online",
            "role",
            "role_display",
            "is_primary",
            "notes",
        )
        read_only_fields = ("id",)


class LockerStatusSerializer(serializers.ModelSerializer):
    is_alarm = serializers.BooleanField(read_only=True)
    is_insecure = serializers.BooleanField(read_only=True)
    device_mac = serializers.CharField(source="device.mac_address", read_only=True, default=None)
    device_is_online = serializers.SerializerMethodField()

    class Meta:
        model = LockerStatus
        fields = (
            "secure",
            "state",
            "reed_closed",
            "latch_locked",
            "ir_broken",
            "mortise_active",
            "item_present",
            "last_trigger",
            "firmware_version",
            "last_status_at",
            "is_alarm",
            "is_insecure",
            "device_mac",
            "device_is_online",
        )

    def get_device_is_online(self, obj):
        return obj.device.is_online if obj.device_id else None


class LockerDetailSerializer(LockerSerializer):
    """Locker + its bound devices + latest lock status, for the management UI."""

    devices = LockerDeviceSerializer(source="device_assignments", many=True, read_only=True)
    status = serializers.SerializerMethodField()

    class Meta(LockerSerializer.Meta):
        fields = LockerSerializer.Meta.fields + ("devices", "status")

    def get_status(self, obj):
        status = getattr(obj, "status", None)
        return LockerStatusSerializer(status).data if status is not None else None


class LockerWriteSerializer(serializers.ModelSerializer):
    """Create/update payload for the locker setup UI.

    ``slug`` auto-derives from the name when omitted (deduped against
    existing lockers); the response echoes the full detail representation
    so the UI gets bound devices + status back without a second fetch.
    """

    slug = serializers.SlugField(max_length=120, required=False, allow_blank=True)

    class Meta:
        model = Locker
        fields = (
            "id",
            "name",
            "slug",
            "location",
            "owning_sig",
            "description",
            "power_source",
            "current_asset",
            "is_high_trust",
            "led_count",
            "required_certifications",
            "is_active",
        )
        read_only_fields = ("id",)

    @staticmethod
    def _unique_slug(name: str, *, exclude_pk=None) -> str:
        base = slugify(name) or "locker"
        slug = base
        n = 2
        qs = Locker.objects.all()
        if exclude_pk is not None:
            qs = qs.exclude(pk=exclude_pk)
        while qs.filter(slug=slug).exists():
            slug = f"{base}-{n}"
            n += 1
        return slug

    def create(self, validated_data):
        if not validated_data.get("slug"):
            validated_data["slug"] = self._unique_slug(validated_data["name"])
        return super().create(validated_data)

    def update(self, instance, validated_data):
        if "slug" in validated_data and not validated_data["slug"]:
            validated_data["slug"] = self._unique_slug(
                validated_data.get("name", instance.name), exclude_pk=instance.pk
            )
        return super().update(instance, validated_data)

    def to_representation(self, instance):
        return LockerDetailSerializer(instance, context=self.context).data


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


class LockStatusEventSerializer(_WebhookEventBase):
    """The firmware's comprehensive ``cabinet_lock/status`` heartbeat
    (~every 10s). Every hardware field is optional so a partial/older
    firmware payload still upserts what it carries."""

    secure = serializers.BooleanField(required=False, allow_null=True)
    state = serializers.CharField(required=False, allow_blank=True, default="")
    reed_closed = serializers.BooleanField(required=False, allow_null=True)
    latch_locked = serializers.BooleanField(required=False, allow_null=True)
    ir_broken = serializers.BooleanField(required=False, allow_null=True)
    mortise_active = serializers.BooleanField(required=False, allow_null=True)
    item_present = serializers.BooleanField(required=False, allow_null=True)
    last_trigger = serializers.CharField(required=False, allow_blank=True, default="")
    firmware_version = serializers.CharField(required=False, allow_blank=True, default="")
