"""DRF serializers for the LOTO data model (oms-78j)."""

from __future__ import annotations

from rest_framework import serializers

from .models import AssetEnergySource, LOTODevice


class LOTODeviceSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source="location.name", read_only=True, allow_null=True)
    assigned_to_username = serializers.CharField(
        source="assigned_to.username", read_only=True, allow_null=True
    )
    device_type_display = serializers.CharField(source="get_device_type_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = LOTODevice
        fields = [
            "id",
            "device_type",
            "device_type_display",
            "label",
            "location",
            "location_name",
            "assigned_to",
            "assigned_to_username",
            "status",
            "status_display",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class LOTODeviceSummarySerializer(serializers.ModelSerializer):
    """Compact representation embedded in AssetEnergySource."""

    device_type_display = serializers.CharField(source="get_device_type_display", read_only=True)

    class Meta:
        model = LOTODevice
        fields = ["id", "device_type", "device_type_display", "label", "status"]
        read_only_fields = fields


class AssetEnergySourceSerializer(serializers.ModelSerializer):
    asset_name = serializers.CharField(source="asset.name", read_only=True)
    source_type_display = serializers.CharField(source="get_source_type_display", read_only=True)
    required_devices_detail = LOTODeviceSummarySerializer(
        source="required_devices", many=True, read_only=True
    )

    class Meta:
        model = AssetEnergySource
        fields = [
            "id",
            "asset",
            "asset_name",
            "source_type",
            "source_type_display",
            "magnitude",
            "isolation_point",
            "required_devices",
            "required_devices_detail",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class AssetLOTORequirementsSerializer(serializers.Serializer):
    """
    Read-only payload for ``GET /assets/<id>/loto-requirements``.

    Combines the legacy Asset.lockout_* fields (already used by the work
    order parity context) with the new structured energy sources so a single
    fetch tells maintainers everything they need.
    """

    asset_id = serializers.UUIDField(read_only=True)
    asset_name = serializers.CharField(read_only=True)
    lockout_type = serializers.CharField(read_only=True, allow_blank=True)
    lockout_type_display = serializers.CharField(read_only=True, allow_blank=True)
    lockout_instructions = serializers.CharField(read_only=True, allow_blank=True)
    lockout_responsible = serializers.CharField(read_only=True, allow_blank=True)
    is_required = serializers.BooleanField(read_only=True)
    energy_sources = AssetEnergySourceSerializer(many=True, read_only=True)
