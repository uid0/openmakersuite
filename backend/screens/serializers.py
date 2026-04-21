"""
Serializers for the screens app.
"""

from rest_framework import serializers

from .models import Screen, ScreenContentBlock, ScreenHeartbeat, SystemMessage


class ScreenContentBlockSerializer(serializers.ModelSerializer):
    block_type_display = serializers.CharField(source="get_block_type_display", read_only=True)

    class Meta:
        model = ScreenContentBlock
        fields = [
            "id",
            "screen",
            "block_type",
            "block_type_display",
            "title",
            "body",
            "config",
            "order",
            "is_enabled",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "block_type_display"]


class SystemMessageSerializer(serializers.ModelSerializer):
    level_display = serializers.CharField(source="get_level_display", read_only=True)
    is_currently_visible = serializers.SerializerMethodField()

    class Meta:
        model = SystemMessage
        fields = [
            "id",
            "title",
            "body",
            "level",
            "level_display",
            "is_active",
            "starts_at",
            "ends_at",
            "created_at",
            "updated_at",
            "is_currently_visible",
        ]
        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
            "level_display",
            "is_currently_visible",
        ]

    def get_is_currently_visible(self, obj):
        return obj.is_currently_visible()


class ScreenHeartbeatSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScreenHeartbeat
        fields = ["id", "reported_at", "user_agent", "client_ip", "content_version"]
        read_only_fields = ["id", "reported_at", "client_ip"]


class ScreenSerializer(serializers.ModelSerializer):
    content_blocks = ScreenContentBlockSerializer(many=True, read_only=True)
    last_heartbeat_at = serializers.SerializerMethodField()
    is_online = serializers.BooleanField(read_only=True)
    sig_name = serializers.CharField(source="sig.name", read_only=True, default="")
    location_name = serializers.CharField(source="location.name", read_only=True, default="")

    class Meta:
        model = Screen
        fields = [
            "id",
            "slug",
            "name",
            "description",
            "location",
            "location_name",
            "sig",
            "sig_name",
            "is_active",
            "rotation_interval_seconds",
            "refresh_interval_seconds",
            "access_token",
            "is_online",
            "last_heartbeat_at",
            "content_blocks",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "access_token",
            "is_online",
            "last_heartbeat_at",
            "content_blocks",
            "sig_name",
            "location_name",
            "created_at",
            "updated_at",
        ]

    def get_last_heartbeat_at(self, obj):
        hb = obj.last_heartbeat
        return hb.reported_at if hb else None


class ScreenStatusSerializer(serializers.ModelSerializer):
    """Lightweight serializer for the status dashboard."""

    is_online = serializers.BooleanField(read_only=True)
    last_heartbeat_at = serializers.SerializerMethodField()
    sig_name = serializers.CharField(source="sig.name", read_only=True, default="")
    location_name = serializers.CharField(source="location.name", read_only=True, default="")

    class Meta:
        model = Screen
        fields = [
            "id",
            "slug",
            "name",
            "is_active",
            "is_online",
            "last_heartbeat_at",
            "sig_name",
            "location_name",
            "updated_at",
        ]

    def get_last_heartbeat_at(self, obj):
        hb = obj.last_heartbeat
        return hb.reported_at if hb else None


class KioskPayloadSerializer(serializers.Serializer):
    """Serializer for the payload consumed by a kiosk display."""

    screen = serializers.DictField()
    system_messages = serializers.ListField(child=serializers.DictField())
    content_blocks = serializers.ListField(child=serializers.DictField())
    generated_at = serializers.DateTimeField()
