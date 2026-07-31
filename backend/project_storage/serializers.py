from django.contrib.auth.models import Group

from rest_framework import serializers
from rest_framework.validators import UniqueTogetherValidator

from fiducials.services.allocator import get_active_tag_id

from .models import ProjectStorageEvent, ProjectStorageStint, StorageSlot


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
    qr_code_url = serializers.SerializerMethodField()
    april_tag_id = serializers.SerializerMethodField()

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
            "qr_code_url",
            "april_tag_id",
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
            "qr_code_url",
            "april_tag_id",
            "created_at",
            "updated_at",
        )

    def get_status(self, obj: ProjectStorageStint) -> str:
        return obj.compute_status()

    def get_qr_code_url(self, obj: ProjectStorageStint) -> str | None:
        if not obj.qr_code:
            return None
        request = self.context.get("request") if hasattr(self, "context") else None
        url = obj.qr_code.url
        if request is not None:
            return request.build_absolute_uri(url)
        return url

    def get_april_tag_id(self, obj: ProjectStorageStint) -> int | None:
        # Prefer the annotation (set by the viewset to avoid an N+1 on
        # list endpoints); fall back to a direct lookup for single objects
        # returned from write actions.
        if hasattr(obj, "active_april_tag_id"):
            return obj.active_april_tag_id
        return get_active_tag_id(obj)

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


# ---------------------------------------------------------------------------
# Storage slots (the physical racking)
# ---------------------------------------------------------------------------


class StorageSlotSerializer(serializers.ModelSerializer):
    """Read/write shape for one slot.

    ``code`` is read-only: the components are authoritative and the model
    recomputes the string on save. The explicit UniqueTogetherValidator
    turns a duplicate slot into a 400 instead of letting the DB constraint
    surface as a 500.
    """

    april_tag_id = serializers.SerializerMethodField()
    # ``default=""`` (the convention used elsewhere in the codebase) keeps the
    # key present for an unowned slot — without it DRF hits an AttributeError
    # walking into the null FK and silently drops the field from the payload.
    owning_group_name = serializers.CharField(
        source="owning_group.name", read_only=True, default=""
    )

    class Meta:
        model = StorageSlot
        fields = (
            "id",
            "code",
            "rack",
            "level",
            "position",
            "requires_pallet_jack",
            "is_active",
            "owning_group",
            "owning_group_name",
            "notes",
            "april_tag_id",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "code",
            "owning_group_name",
            "april_tag_id",
            "created_at",
            "updated_at",
        )
        validators = [
            UniqueTogetherValidator(
                queryset=StorageSlot.objects.all(),
                fields=("rack", "level", "position"),
                message="A storage slot already exists at that rack/level/position.",
            )
        ]

    def validate_level(self, value: str) -> str:
        # Normalize before the unique-together check runs, so posting "1a1"
        # collides with an existing "1A1" at the serializer instead of at
        # the DB.
        return (value or "").upper()

    def get_april_tag_id(self, obj: StorageSlot) -> int | None:
        # Prefer the viewset's annotation (no N+1 on list); fall back to a
        # direct lookup for objects returned from write actions.
        if hasattr(obj, "active_april_tag_id"):
            return obj.active_april_tag_id
        return get_active_tag_id(obj)


class RackLevelSpecSerializer(serializers.Serializer):
    """One level of a rack in a bulk-generate request."""

    level = serializers.RegexField(
        StorageSlot.LEVEL_PATTERN,
        error_messages={"invalid": "Level must be a single letter (A-Z)."},
    )
    # Capped so a fat-fingered "1000" can't spawn a rack that swallows the
    # whole tag family in one request.
    positions = serializers.IntegerField(min_value=1, max_value=100)
    requires_pallet_jack = serializers.BooleanField(required=False, default=False)

    def validate_level(self, value: str) -> str:
        return value.upper()


class GenerateRackSerializer(serializers.Serializer):
    """Bulk-generate payload: one rack, a spec per level."""

    rack = serializers.IntegerField(min_value=1)
    levels = serializers.ListField(
        child=RackLevelSpecSerializer(),
        allow_empty=False,
        max_length=26,
    )
    owning_group = serializers.PrimaryKeyRelatedField(
        queryset=Group.objects.all(),
        required=False,
        allow_null=True,
    )
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_levels(self, value: list[dict]) -> list[dict]:
        seen = [spec["level"] for spec in value]
        duplicates = sorted({level for level in seen if seen.count(level) > 1})
        if duplicates:
            raise serializers.ValidationError(
                f"Each level may appear at most once: {', '.join(duplicates)} repeated."
            )
        return value


class SlotCardBatchSerializer(serializers.Serializer):
    """Which slots to print cards for: an explicit list, or a whole rack.

    Two selection modes because the two real jobs are different. Racking a
    new aisle prints every slot of a rack in code order (the sheets come off
    the printer in the order they get stuck on the uprights); replacing a
    handful of scuffed cards prints exactly those slots, in the order the
    caller listed them. Mixing the two would only make "what did I just
    print?" ambiguous, so exactly one mode is required.
    """

    slot_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=False,
        # 300 cards = 100 sheets. Past that the request is a rack print that
        # should say so, and the renderer would be holding a lot of PNGs.
        max_length=300,
    )
    rack = serializers.IntegerField(min_value=1, required=False)
    level = serializers.RegexField(
        StorageSlot.LEVEL_PATTERN,
        required=False,
        error_messages={"invalid": "Level must be a single letter (A-Z)."},
    )
    # A rack print covers the slots in service. Retired slots keep their
    # marker (see StorageSlot.is_active) and can still be reprinted on
    # purpose, but they shouldn't ride along with a rack's sheets.
    include_inactive = serializers.BooleanField(required=False, default=False)

    def validate_level(self, value: str) -> str:
        return (value or "").upper()

    def validate(self, attrs: dict) -> dict:
        has_ids = bool(attrs.get("slot_ids"))
        has_rack = attrs.get("rack") is not None
        if has_ids and has_rack:
            raise serializers.ValidationError("Provide either slot_ids or a rack filter, not both.")
        if not has_ids and not has_rack:
            raise serializers.ValidationError("Provide slot_ids or a rack to print.")
        if attrs.get("level") and not has_rack:
            raise serializers.ValidationError({"level": "level narrows a rack — send rack too."})
        return attrs
