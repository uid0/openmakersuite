from rest_framework import serializers

from .models import PMSchedule, PMServiceLog


class PMServiceLogSerializer(serializers.ModelSerializer):
    performed_by_username = serializers.CharField(
        source="performed_by.username", read_only=True, allow_null=True
    )

    class Meta:
        model = PMServiceLog
        fields = [
            "id",
            "schedule",
            "performed_at",
            "performed_by",
            "performed_by_username",
            "notes",
            "created_at",
        ]
        read_only_fields = ["id", "created_at", "performed_by_username"]


class PMScheduleSerializer(serializers.ModelSerializer):
    asset_name = serializers.CharField(source="asset.name", read_only=True)
    location_name = serializers.SerializerMethodField()
    last_performed_at = serializers.SerializerMethodField()
    days_since_last = serializers.IntegerField(read_only=True)
    days_until_due = serializers.IntegerField(read_only=True)
    status = serializers.CharField(read_only=True)

    class Meta:
        model = PMSchedule
        fields = [
            "id",
            "asset",
            "asset_name",
            "location_name",
            "task_name",
            "description",
            "interval_days",
            "is_active",
            "last_performed_at",
            "days_since_last",
            "days_until_due",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "asset_name",
            "location_name",
            "last_performed_at",
            "days_since_last",
            "days_until_due",
            "status",
            "created_at",
            "updated_at",
        ]

    def get_location_name(self, obj: PMSchedule) -> str | None:
        loc = getattr(obj.asset, "location", None)
        return loc.name if loc is not None else None

    def get_last_performed_at(self, obj: PMSchedule):
        log = obj.last_log()
        return log.performed_at if log is not None else None


class PMBoardEntrySerializer(serializers.Serializer):
    """Compact projection used by the board endpoint — Inkplate-friendly.

    Same shape as `PMScheduleSerializer` minus the audit timestamps and
    free-text description fields, so the device firmware can deserialize
    into a fixed struct.
    """

    id = serializers.UUIDField()
    asset_id = serializers.UUIDField()
    asset_name = serializers.CharField()
    location_name = serializers.CharField(allow_null=True)
    task_name = serializers.CharField()
    interval_days = serializers.IntegerField()
    last_performed_at = serializers.DateTimeField(allow_null=True)
    days_since_last = serializers.IntegerField(allow_null=True)
    days_until_due = serializers.IntegerField(allow_null=True)
    status = serializers.CharField()
