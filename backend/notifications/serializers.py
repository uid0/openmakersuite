"""
Serializers for notification API.
"""

from rest_framework import serializers

from .models import Notification, NotificationPreference


class NotificationSerializer(serializers.ModelSerializer):
    """Serializer for Notification model."""

    class Meta:
        model = Notification
        fields = [
            "id",
            "type",
            "title",
            "message",
            "read",
            "created_at",
            "action_url",
            "metadata",
        ]
        read_only_fields = ["id", "created_at"]


class NotificationCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating notifications (admin/system use)."""

    class Meta:
        model = Notification
        fields = [
            "user",
            "type",
            "title",
            "message",
            "action_url",
            "metadata",
        ]


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    """Serializer for notification preferences."""

    class Meta:
        model = NotificationPreference
        fields = [
            "id",
            "email_enabled",
            "in_app_enabled",
            "supply_alerts",
            "maintenance_alerts",
            "order_updates",
            "system_notifications",
            "recent_pages_limit",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
