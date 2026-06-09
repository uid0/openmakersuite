"""DRF serializers for the storage_vision setup surfaces.

VisionArea + VisionSlot are plain ModelSerializers. VisionCamera has
two shapes:

  - :class:`VisionCameraSerializer` — the public read shape: no token,
    only the 16-hex fingerprint and last-seen metadata.
  - :class:`VisionCameraTokenSerializer` — the response shape for the
    one create / rotate-token call that returns the RAW bearer to the
    operator. The viewset selects this serializer for those two paths
    only; subsequent retrieves go through the public read shape.
"""

from __future__ import annotations

from rest_framework import serializers

from .models import VisionArea, VisionCamera, VisionSlot


class VisionAreaSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source="location.name", read_only=True)

    class Meta:
        model = VisionArea
        fields = [
            "id",
            "name",
            "location",
            "location_name",
            "description",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class VisionSlotSerializer(serializers.ModelSerializer):
    area_name = serializers.CharField(source="area.name", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)

    class Meta:
        model = VisionSlot
        fields = [
            "id",
            "area",
            "area_name",
            "item",
            "item_name",
            "marker_code",
            "empty_low_confidence_threshold",
            "notes",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class VisionCameraSerializer(serializers.ModelSerializer):
    """Public read shape. NEVER returns the raw bearer token."""

    area_name = serializers.CharField(source="area.name", read_only=True, allow_null=True)

    class Meta:
        model = VisionCamera
        fields = [
            "id",
            "name",
            "area",
            "area_name",
            "token_fingerprint",
            "last_seen_at",
            "last_seen_status",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "token_fingerprint",
            "last_seen_at",
            "last_seen_status",
            "created_at",
            "updated_at",
        ]


class VisionCameraTokenSerializer(serializers.ModelSerializer):
    """Create / rotate response shape — includes the raw bearer ONCE."""

    raw_token = serializers.CharField(read_only=True)
    area_name = serializers.CharField(source="area.name", read_only=True, allow_null=True)

    class Meta:
        model = VisionCamera
        fields = [
            "id",
            "name",
            "area",
            "area_name",
            "token_fingerprint",
            "raw_token",
            "last_seen_at",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "token_fingerprint",
            "raw_token",
            "last_seen_at",
            "created_at",
            "updated_at",
        ]
