"""
Serializers for notification API.
"""

from rest_framework import serializers

from .models import KnownDevice, Notification, NotificationPreference


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


class KnownDeviceSerializer(serializers.ModelSerializer):
    """Read-only serializer for a user's known devices.

    The opaque ``device_token`` and ``fingerprint_hash`` are intentionally never
    exposed — they are credentials/identifiers, not display data. ``is_current``
    marks the device whose signed cookie accompanies *this* request, computed
    from ``context["current_device_token"]`` (set by the view).
    """

    is_current = serializers.SerializerMethodField()

    class Meta:
        model = KnownDevice
        fields = [
            "id",
            "user_agent",
            "ip_address",
            "label",
            "first_seen",
            "last_seen",
            "is_current",
        ]
        read_only_fields = fields

    def get_is_current(self, obj) -> bool:
        current = self.context.get("current_device_token")
        return bool(current) and obj.device_token == current


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
