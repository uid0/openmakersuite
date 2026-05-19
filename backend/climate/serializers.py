"""DRF serializers for climate models."""

from __future__ import annotations

from rest_framework import serializers

from .models import Thermostat


class ThermostatSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source="location.name", read_only=True)
    controls_location_name = serializers.CharField(
        source="controls_location.name",
        read_only=True,
        default=None,
    )
    controlled_asset_name = serializers.CharField(
        source="controlled_asset.name",
        read_only=True,
        default=None,
    )

    class Meta:
        model = Thermostat
        fields = [
            "id",
            "label",
            "location",
            "location_name",
            "controls_location",
            "controls_location_name",
            "controlled_asset",
            "controlled_asset_name",
            "manufacturer",
            "model",
            "notes",
            "needs_review",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["needs_review", "created_at", "updated_at"]
