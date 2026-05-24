from rest_framework import serializers

from .models import ProjectStorageEvent, ProjectStorageStint


class ProjectStorageEventSerializer(serializers.ModelSerializer):
    actor_username = serializers.SerializerMethodField()

    class Meta:
        model = ProjectStorageEvent
        fields = (
            "id",
            "event_type",
            "actor_username",
            "actor_label",
            "note",
            "created_at",
        )

    def get_actor_username(self, obj: ProjectStorageEvent) -> str:
        return obj.actor.username if obj.actor else ""


class ProjectStorageStintSerializer(serializers.ModelSerializer):
    status = serializers.SerializerMethodField()
    display_name = serializers.ReadOnlyField()
    purgatory_at = serializers.ReadOnlyField()
    expiry_week = serializers.SerializerMethodField()
    expiry_day_of_year = serializers.SerializerMethodField()
    events = ProjectStorageEventSerializer(many=True, read_only=True)

    class Meta:
        model = ProjectStorageStint
        fields = (
            "id",
            "stint_id",
            "username",
            "first_name",
            "last_name",
            "email",
            "display_name",
            "project_title",
            "started_at",
            "expires_at",
            "removed_at",
            "notice_sent_at",
            "moved_to_purgatory_at",
            "storage_location_name",
            "purgatory_location_name",
            "notes",
            "status",
            "purgatory_at",
            "expiry_week",
            "expiry_day_of_year",
            "events",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "stint_id",
            "expires_at",
            "removed_at",
            "notice_sent_at",
            "moved_to_purgatory_at",
            "status",
            "purgatory_at",
            "expiry_week",
            "expiry_day_of_year",
            "events",
            "created_at",
            "updated_at",
        )

    def get_status(self, obj: ProjectStorageStint) -> str:
        return obj.compute_status()

    def get_expiry_week(self, obj: ProjectStorageStint) -> int:
        return obj.expiry_week_and_day[0]

    def get_expiry_day_of_year(self, obj: ProjectStorageStint) -> int:
        return obj.expiry_week_and_day[1]


class StartStintSerializer(serializers.Serializer):
    """Self-service kiosk payload for starting a new stint."""

    username = serializers.CharField(max_length=64)
    first_name = serializers.CharField(max_length=64, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=64, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    project_title = serializers.CharField(max_length=120, required=False, allow_blank=True)
    storage_location_name = serializers.CharField(max_length=120, required=False, allow_blank=True)
