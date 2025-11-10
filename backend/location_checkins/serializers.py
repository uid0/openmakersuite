"""
Serializers for location check-in API.
"""

from rest_framework import serializers

from .models import LocationCheckIn, LocationFeedback, LocationTask, SecurityReport


class LocationCheckInSerializer(serializers.ModelSerializer):
    """Serializer for location check-ins."""

    location_name = serializers.CharField(source="location.name", read_only=True)
    user_username = serializers.CharField(source="user.username", read_only=True, allow_null=True)

    class Meta:
        model = LocationCheckIn
        fields = [
            "id",
            "location",
            "location_name",
            "checkin_type",
            "user",
            "user_username",
            "checked_in_at",
            "notes",
        ]
        read_only_fields = ["id", "checked_in_at"]


class LocationFeedbackSerializer(serializers.ModelSerializer):
    """Serializer for location feedback."""

    location_name = serializers.CharField(source="location.name", read_only=True)
    user_username = serializers.CharField(source="user.username", read_only=True, allow_null=True)

    class Meta:
        model = LocationFeedback
        fields = [
            "id",
            "location",
            "location_name",
            "feedback_type",
            "message",
            "user",
            "user_username",
            "submitted_at",
            "is_resolved",
        ]
        read_only_fields = ["id", "submitted_at", "is_resolved"]


class SecurityReportSerializer(serializers.ModelSerializer):
    """Serializer for safety and cleaning reports."""

    location_name = serializers.CharField(source="location.name", read_only=True)
    user_username = serializers.CharField(source="user.username", read_only=True, allow_null=True)
    resolved_by_username = serializers.CharField(
        source="resolved_by.username", read_only=True, allow_null=True
    )

    class Meta:
        model = SecurityReport
        fields = [
            "id",
            "location",
            "location_name",
            "report_type",
            "is_urgent",
            "description",
            "user",
            "user_username",
            "reported_at",
            "is_resolved",
            "resolved_at",
            "resolved_by",
            "resolved_by_username",
        ]
        read_only_fields = [
            "id",
            "reported_at",
            "is_resolved",
            "resolved_at",
            "resolved_by",
        ]


class LocationTaskSerializer(serializers.ModelSerializer):
    """Serializer for location tasks."""

    location_name = serializers.CharField(source="location.name", read_only=True)
    created_by_username = serializers.CharField(
        source="created_by.username", read_only=True, allow_null=True
    )
    assigned_to_username = serializers.CharField(
        source="assigned_to.username", read_only=True, allow_null=True
    )
    completed_by_username = serializers.CharField(
        source="completed_by.username", read_only=True, allow_null=True
    )

    class Meta:
        model = LocationTask
        fields = [
            "id",
            "location",
            "location_name",
            "title",
            "description",
            "status",
            "created_from_feedback",
            "created_from_security_report",
            "created_by",
            "created_by_username",
            "assigned_to",
            "assigned_to_username",
            "completed_by",
            "completed_by_username",
            "created_at",
            "completed_at",
            "due_date",
        ]
        read_only_fields = [
            "id",
            "created_at",
            "completed_at",
        ]


class LocationTaskCompleteSerializer(serializers.Serializer):
    """Serializer for completing a task."""

    notes = serializers.CharField(required=False, allow_blank=True)
