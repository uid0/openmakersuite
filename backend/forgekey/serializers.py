"""
Serializers for ForgeKey models.
"""

from rest_framework import serializers

from .models import (
    AssetAuthorization,
    AssetDevice,
    CertificateAuthority,
    DeviceCertificate,
    DeviceCommand,
    DeviceFirmwareUpdate,
    DeviceLockout,
    DeviceType,
    DeviceUsage,
    EPaperDisplay,
    EpaperFirmwareRollout,
    ESP32Device,
    FirmwareBuild,
    FirmwareRollout,
    FirmwareVersion,
    ForgeKeyAuditEvent,
    IndicatorBinding,
    OccupancyEvent,
    OperationalMode,
    PowerMeterReading,
    RoomOperationalMode,
    TemperatureReading,
)


class DeviceTypeSerializer(serializers.ModelSerializer):
    """Serializer for DeviceType model."""

    class Meta:
        model = DeviceType
        fields = "__all__"


class ESP32DeviceSerializer(serializers.ModelSerializer):
    """Serializer for ESP32Device model."""

    device_type_name = serializers.CharField(source="device_type.name", read_only=True)

    class Meta:
        model = ESP32Device
        fields = "__all__"
        read_only_fields = ["id", "created_at", "updated_at"]


class AssetDeviceSerializer(serializers.ModelSerializer):
    """Serializer for AssetDevice model."""

    asset_name = serializers.CharField(source="asset.name", read_only=True)
    device_mac_address = serializers.CharField(source="device.mac_address", read_only=True)
    device_name = serializers.CharField(source="device.name", read_only=True)

    class Meta:
        model = AssetDevice
        fields = "__all__"
        read_only_fields = ["created_at"]


class OperationalModeSerializer(serializers.ModelSerializer):
    """Serializer for OperationalMode model."""

    asset_name = serializers.CharField(source="asset.name", read_only=True)
    asset_location_name = serializers.CharField(
        source="asset.location.name", read_only=True, allow_null=True
    )
    classroom_mode_enabled_by_username = serializers.CharField(
        source="classroom_mode_enabled_by.username", read_only=True
    )

    class Meta:
        model = OperationalMode
        fields = "__all__"
        read_only_fields = ["updated_at"]


class RoomOperationalModeSerializer(serializers.ModelSerializer):
    """Serializer for RoomOperationalMode — admin-set room status."""

    location_name = serializers.CharField(source="location.name", read_only=True)
    updated_by_username = serializers.CharField(
        source="updated_by.username", read_only=True, default=None
    )

    class Meta:
        model = RoomOperationalMode
        fields = "__all__"
        read_only_fields = ["updated_at", "updated_by"]


class IndicatorBindingSerializer(serializers.ModelSerializer):
    """Serializer for IndicatorBinding — indicator device ↔ asset XOR room.

    Validates the asset-XOR-room invariant and indicator device type at the API
    layer so clients get a 400 with a clear message rather than a DB
    IntegrityError. ``last_*`` fields are read-only state set by the sync path.
    """

    device_mac_address = serializers.CharField(source="device.mac_address", read_only=True)
    device_name = serializers.CharField(source="device.name", read_only=True)
    asset_name = serializers.CharField(source="asset.name", read_only=True, default=None)
    location_name = serializers.CharField(source="location.name", read_only=True, default=None)

    class Meta:
        model = IndicatorBinding
        fields = "__all__"
        read_only_fields = [
            "id",
            "last_status",
            "last_presentation",
            "last_synced_at",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        instance = self.instance
        asset = attrs.get("asset", instance.asset if instance else None)
        location = attrs.get("location", instance.location if instance else None)
        if bool(asset) == bool(location):
            raise serializers.ValidationError("Exactly one of asset or location must be set.")
        device = attrs.get("device", instance.device if instance else None)
        if device is not None and device.device_type.code != DeviceType.TYPE_INDICATOR:
            raise serializers.ValidationError(
                {"device": "Bound device must be an indicator-type device."}
            )
        return attrs


class AssetAuthorizationSerializer(serializers.ModelSerializer):
    """Serializer for AssetAuthorization model."""

    asset_name = serializers.CharField(source="asset.name", read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)
    authorized_by_username = serializers.CharField(source="authorized_by.username", read_only=True)

    class Meta:
        model = AssetAuthorization
        fields = "__all__"
        read_only_fields = ["authorized_at"]


class DeviceLockoutSerializer(serializers.ModelSerializer):
    """Serializer for DeviceLockout model."""

    asset_name = serializers.CharField(source="asset.name", read_only=True)
    locked_by_username = serializers.CharField(source="locked_by.username", read_only=True)
    unlocked_by_username = serializers.CharField(source="unlocked_by.username", read_only=True)

    class Meta:
        model = DeviceLockout
        fields = "__all__"
        read_only_fields = ["id", "locked_at", "lockout_level"]


class DeviceUsageSerializer(serializers.ModelSerializer):
    """Serializer for DeviceUsage model."""

    asset_name = serializers.CharField(source="asset.name", read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = DeviceUsage
        fields = "__all__"
        read_only_fields = ["id", "started_at"]


class PowerMeterReadingSerializer(serializers.ModelSerializer):
    """Serializer for PowerMeterReading model."""

    asset_name = serializers.CharField(source="asset.name", read_only=True)
    device_mac_address = serializers.CharField(source="device.mac_address", read_only=True)

    class Meta:
        model = PowerMeterReading
        fields = "__all__"
        read_only_fields = ["id", "timestamp"]


class FirmwareVersionSerializer(serializers.ModelSerializer):
    """Serializer for FirmwareVersion model."""

    device_type_name = serializers.CharField(source="device_type.name", read_only=True)
    # device_type_code lets the frontend pick the right rollout API path
    # without string-matching on the human-readable name.
    device_type_code = serializers.CharField(source="device_type.code", read_only=True)
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)

    class Meta:
        model = FirmwareVersion
        fields = "__all__"
        read_only_fields = ["id", "created_at"]


class FirmwareBuildSerializer(serializers.ModelSerializer):
    """A self-hosted firmware build request + its outcome."""

    device_type_name = serializers.CharField(source="device_type.name", read_only=True)
    requested_by_username = serializers.CharField(
        source="requested_by.username", read_only=True, default=None
    )
    firmware_version_string = serializers.CharField(
        source="firmware_version.version", read_only=True, default=None
    )

    class Meta:
        model = FirmwareBuild
        fields = [
            "id",
            "device_type",
            "device_type_name",
            "pio_env",
            "source_ref",
            "version",
            "mandatory",
            "release_notes",
            "status",
            "ca_fingerprint",
            "commit_sha",
            "log",
            "error_message",
            "firmware_version",
            "firmware_version_string",
            "requested_by",
            "requested_by_username",
            "requested_at",
            "started_at",
            "completed_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "ca_fingerprint",
            "commit_sha",
            "log",
            "error_message",
            "firmware_version",
            "requested_by",
            "requested_at",
            "started_at",
            "completed_at",
        ]


class OccupancyEventSerializer(serializers.ModelSerializer):
    """Serializer for OccupancyEvent rows, used by the device-detail chart."""

    occupancy_delta = serializers.IntegerField(read_only=True)

    class Meta:
        model = OccupancyEvent
        fields = [
            "id",
            "device",
            "sensor_kind",
            "count_in",
            "count_out",
            "occupancy_delta",
            "event_timestamp_utc",
            "ingested_at",
            "raw_payload",
        ]
        read_only_fields = ["id", "ingested_at", "occupancy_delta"]


class TemperatureReadingSerializer(serializers.ModelSerializer):
    """Serializer for TemperatureReading rows, used by the device-detail chart."""

    class Meta:
        model = TemperatureReading
        fields = [
            "id",
            "device",
            "sensor_kind",
            "temperature_c",
            "humidity_percent",
            "recorded_at",
            "raw_payload",
        ]
        read_only_fields = ["id", "recorded_at"]


class DeviceCommandSerializer(serializers.ModelSerializer):
    """Serializer for the recent-commands history table on device-detail."""

    sent_by_username = serializers.CharField(source="sent_by.username", read_only=True)
    effective_ack_status = serializers.CharField(read_only=True)

    class Meta:
        model = DeviceCommand
        fields = [
            "id",
            "command",
            "payload",
            "sent_by",
            "sent_by_username",
            "sent_at",
            "ack_status",
            "effective_ack_status",
            "ack_at",
            "ack_payload",
        ]
        read_only_fields = fields


class DeviceFirmwareUpdateSerializer(serializers.ModelSerializer):
    """Serializer for DeviceFirmwareUpdate model."""

    device_mac_address = serializers.CharField(source="device.mac_address", read_only=True)
    firmware_version_string = serializers.CharField(
        source="firmware_version.version", read_only=True
    )
    requested_by_username = serializers.CharField(source="requested_by.username", read_only=True)

    class Meta:
        model = DeviceFirmwareUpdate
        fields = "__all__"
        read_only_fields = ["id", "requested_at", "updated_at"]


class EPaperDisplaySerializer(serializers.ModelSerializer):
    """Serializer for the e-paper panels management screen."""

    asset_name = serializers.SerializerMethodField()
    asset_tag = serializers.SerializerMethodField()
    device_mac_address = serializers.SerializerMethodField()
    is_low_battery = serializers.BooleanField(read_only=True)
    target_firmware_version_string = serializers.CharField(
        source="target_firmware_version.version", read_only=True, default=None
    )

    class Meta:
        model = EPaperDisplay
        fields = [
            "id",
            "device",
            "device_mac_address",
            "asset",
            "asset_name",
            "asset_tag",
            "battery_percent",
            "is_low_battery",
            "last_battery_at",
            "battery_available",
            "battery_unavailable_reason",
            "last_health_at",
            "last_image_etag",
            "last_image_at",
            "firmware_version",
            "target_firmware_version",
            "target_firmware_version_string",
            "event_face_weight",
            "pm_face_weight",
            "rotation_counter",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_asset_name(self, obj):
        return obj.asset.name if obj.asset_id else None

    def get_asset_tag(self, obj):
        return obj.asset.asset_tag if obj.asset_id else None

    def get_device_mac_address(self, obj):
        return obj.device.mac_address if obj.device_id else None


class FirmwareRolloutSerializer(serializers.ModelSerializer):
    """Serializer for the firmware rollout campaigns management screen."""

    firmware_version_string = serializers.CharField(
        source="firmware_version.version", read_only=True
    )
    device_type_name = serializers.CharField(
        source="firmware_version.device_type.name", read_only=True
    )
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)
    progress = serializers.SerializerMethodField()

    class Meta:
        model = FirmwareRollout
        fields = [
            "id",
            "firmware_version",
            "firmware_version_string",
            "device_type_name",
            "name",
            "status",
            "batch_size_percent",
            "interval_minutes",
            "created_by",
            "created_by_username",
            "created_at",
            "updated_at",
            "started_at",
            "completed_at",
            "last_advanced_at",
            "progress",
        ]
        read_only_fields = [
            "id",
            "status",
            "created_by",
            "created_at",
            "updated_at",
            "started_at",
            "completed_at",
            "last_advanced_at",
        ]

    def get_progress(self, obj):
        from .services.firmware_rollout import rollout_progress

        return rollout_progress(obj)


class EpaperFirmwareRolloutSerializer(serializers.ModelSerializer):
    """ePaper rollout campaign — same shape as the MQTT rollout serializer
    so the frontend rollouts page can render both lists uniformly."""

    firmware_version_string = serializers.CharField(
        source="firmware_version.version", read_only=True
    )
    device_type_name = serializers.CharField(
        source="firmware_version.device_type.name", read_only=True
    )
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)
    progress = serializers.SerializerMethodField()

    class Meta:
        model = EpaperFirmwareRollout
        fields = [
            "id",
            "firmware_version",
            "firmware_version_string",
            "device_type_name",
            "name",
            "status",
            "batch_size_percent",
            "interval_minutes",
            "created_by",
            "created_by_username",
            "created_at",
            "updated_at",
            "started_at",
            "completed_at",
            "last_advanced_at",
            "progress",
        ]
        read_only_fields = [
            "id",
            "status",
            "created_by",
            "created_at",
            "updated_at",
            "started_at",
            "completed_at",
            "last_advanced_at",
        ]

    def get_progress(self, obj):
        # Inline (not a separate service fn) — the calculation is trivial
        # and the result shape matches rollout_progress's so the frontend
        # can reuse the same display component.
        target_total = obj.target_displays().count()
        on_target = (
            obj.target_displays().filter(target_firmware_version=obj.firmware_version).count()
        )
        return {
            "target_total": target_total,
            "promoted": on_target,
            "remaining": max(0, target_total - on_target),
        }


class CertificateAuthoritySerializer(serializers.ModelSerializer):
    """Read-only view of an internal CA, with the CN + fingerprint parsed from
    the stored certificate. Active-CA rows also carry the device-cert counts."""

    common_name = serializers.SerializerMethodField()
    fingerprint_sha256 = serializers.SerializerMethodField()
    active_cert_count = serializers.SerializerMethodField()
    revoked_cert_count = serializers.SerializerMethodField()

    class Meta:
        model = CertificateAuthority
        fields = [
            "id",
            "name",
            "common_name",
            "fingerprint_sha256",
            "not_before",
            "not_after",
            "is_active",
            "created_at",
            "active_cert_count",
            "revoked_cert_count",
        ]

    @staticmethod
    def _cert(obj):
        from cryptography import x509

        try:
            return x509.load_pem_x509_certificate(obj.cert_pem.encode("utf-8"))
        except Exception:
            return None

    def get_common_name(self, obj):
        cert = self._cert(obj)
        if cert is None:
            return None
        return next(
            (a.value for a in cert.subject if a.oid.dotted_string == "2.5.4.3"),
            None,
        )

    def get_fingerprint_sha256(self, obj):
        from cryptography.hazmat.primitives import hashes

        cert = self._cert(obj)
        return cert.fingerprint(hashes.SHA256()).hex() if cert is not None else None

    def get_active_cert_count(self, obj):
        if not obj.is_active:
            return None
        return DeviceCertificate.objects.filter(revoked_at__isnull=True).count()

    def get_revoked_cert_count(self, obj):
        if not obj.is_active:
            return None
        return DeviceCertificate.objects.filter(revoked_at__isnull=False).count()


class DeviceCertificateSerializer(serializers.ModelSerializer):
    """Read-only view of an issued device (mTLS) certificate + its lifecycle
    status."""

    device_chip_id = serializers.CharField(source="device.device_id", read_only=True, default=None)
    status = serializers.SerializerMethodField()

    class Meta:
        model = DeviceCertificate
        fields = [
            "id",
            "device",
            "device_chip_id",
            "serial",
            "subject",
            "fingerprint_sha256",
            "not_before",
            "not_after",
            "revoked_at",
            "issued_by",
            "created_at",
            "status",
        ]

    def get_status(self, obj):
        from django.utils import timezone

        if obj.revoked_at is not None:
            return "revoked"
        if obj.not_after is not None and obj.not_after < timezone.now():
            return "expired"
        return "active"


class ForgeKeyAuditEventSerializer(serializers.ModelSerializer):
    """Read-only serializer for the ForgeKey audit log (access-control surface).

    Backs the access/denial log the access-control frontend (op-tup) consumes.
    Exposes the human-readable action label plus actor/asset/device names so the
    UI can render rows without a second round-trip.
    """

    action_display = serializers.CharField(source="get_action_display", read_only=True)
    actor_username = serializers.CharField(source="actor.username", read_only=True, default=None)
    asset_name = serializers.CharField(source="asset.name", read_only=True, default=None)
    device_mac = serializers.CharField(source="device.mac_address", read_only=True, default=None)

    class Meta:
        model = ForgeKeyAuditEvent
        fields = [
            "id",
            "created_at",
            "action",
            "action_display",
            "actor",
            "actor_username",
            "asset",
            "asset_name",
            "device",
            "device_mac",
            "notes",
            "metadata",
        ]
        read_only_fields = fields
