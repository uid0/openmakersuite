"""DRF serializers for electrical circuits and network drops."""

from rest_framework import serializers

from .models import Breaker, LightSwitch, NetworkDrop, Outlet


class BreakerSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source="location.name", read_only=True)

    class Meta:
        model = Breaker
        fields = [
            "id",
            "location",
            "location_name",
            "panel",
            "breaker_number",
            "amperage",
            "voltage",
            "poles",
            "description",
            "notes",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class OutletSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source="location.name", read_only=True)
    breaker_label = serializers.SerializerMethodField()

    class Meta:
        model = Outlet
        fields = [
            "id",
            "location",
            "location_name",
            "identifier",
            "breaker",
            "breaker_label",
            "outlet_type",
            "description",
            "plugged_in_notes",
            "photo",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_breaker_label(self, obj):
        if obj.breaker_id is None:
            return None
        b = obj.breaker
        return f"{b.panel} / {b.breaker_number}"


class LightSwitchSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source="location.name", read_only=True)
    controls_location_name = serializers.CharField(
        source="controls_location.name", read_only=True, allow_null=True
    )
    breaker_label = serializers.SerializerMethodField()

    class Meta:
        model = LightSwitch
        fields = [
            "id",
            "location",
            "location_name",
            "identifier",
            "controls_location",
            "controls_location_name",
            "breaker",
            "breaker_label",
            "description",
            "notes",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_breaker_label(self, obj):
        if obj.breaker_id is None:
            return None
        b = obj.breaker
        return f"{b.panel} / {b.breaker_number}"


class NetworkDropSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source="location.name", read_only=True)

    class Meta:
        model = NetworkDrop
        fields = [
            "id",
            "location",
            "location_name",
            "identifier",
            "drop_type",
            "patch_panel",
            "patch_port",
            "mac_address",
            "ip_address",
            "description",
            "notes",
            "photo",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
