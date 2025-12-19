"""
Serializers for dashboard widgets.
"""

from rest_framework import serializers

from .models import DashboardWidget


class DashboardWidgetSerializer(serializers.ModelSerializer):
    """Serializer for dashboard widget configuration."""

    class Meta:
        model = DashboardWidget
        fields = [
            "id",
            "widget_type",
            "position_x",
            "position_y",
            "width",
            "height",
            "is_visible",
            "order",
            "settings",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_widget_type(self, value):
        """Validate widget type is one of the allowed types."""
        valid_types = [choice[0] for choice in DashboardWidget.WIDGET_TYPES]
        if value not in valid_types:
            raise serializers.ValidationError(
                f"Invalid widget type. Must be one of: {', '.join(valid_types)}"
            )
        return value


class WidgetDataSerializer(serializers.Serializer):
    """Base serializer for widget data responses."""

    timestamp = serializers.DateTimeField()
    data = serializers.JSONField()
