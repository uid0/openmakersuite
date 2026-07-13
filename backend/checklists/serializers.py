"""
Serializers for checklist models.
"""

from rest_framework import serializers

from inventory.models import count_present_targets
from inventory.serializer_fields import TypedTargetField

from .models import Checklist, ChecklistCompletion, ChecklistStep, ChecklistStepCompletion


class ChecklistStepSerializer(serializers.ModelSerializer):
    """Serializer for checklist steps."""

    # Additive typed-target summary (#884): the flat asset/location/inventory_item
    # PK fields are unchanged; this exposes {target_type, target_id} alongside.
    target = TypedTargetField()

    class Meta:
        model = ChecklistStep
        fields = [
            "id",
            "step_number",
            "name",
            "asset",
            "location",
            "inventory_item",
            "target",
            "required",
            "requires_photo",
            "notes",
        ]
        read_only_fields = ["id"]


class ChecklistListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing checklists."""

    sig_name = serializers.CharField(source="sig.name", read_only=True)
    step_count = serializers.SerializerMethodField()

    class Meta:
        model = Checklist
        fields = [
            "id",
            "name",
            "description",
            "sig",
            "sig_name",
            "is_active",
            "is_public",
            "step_count",
        ]
        read_only_fields = ["id"]

    def get_step_count(self, obj):
        """Get the number of steps in this checklist."""
        return obj.steps.count()


class ChecklistSerializer(serializers.ModelSerializer):
    """Full serializer for checklist with steps."""

    steps = ChecklistStepSerializer(many=True, read_only=True)
    sig_name = serializers.CharField(source="sig.name", read_only=True)
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)

    class Meta:
        model = Checklist
        fields = [
            "id",
            "name",
            "description",
            "sig",
            "sig_name",
            "is_active",
            "is_public",
            "created_by",
            "created_by_username",
            "steps",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class ChecklistStepCompletionSerializer(serializers.ModelSerializer):
    """Serializer for step completion records."""

    step_name = serializers.CharField(source="step.name", read_only=True)
    step_number = serializers.IntegerField(source="step.step_number", read_only=True)
    scanned_asset_name = serializers.CharField(source="scanned_asset.name", read_only=True)
    scanned_location_name = serializers.CharField(source="scanned_location.name", read_only=True)
    scanned_item_name = serializers.CharField(source="scanned_item.name", read_only=True)
    # Additive typed-target summary (#884): the flat scanned_* PK + _name fields
    # are unchanged; this exposes {target_type, target_id} for what was scanned.
    scanned_target = TypedTargetField(object_attr="scanned_target", type_attr="scanned_target_type")
    photo_url = serializers.SerializerMethodField()

    class Meta:
        model = ChecklistStepCompletion
        fields = [
            "id",
            "step",
            "step_name",
            "step_number",
            "scanned_at",
            "scanned_asset",
            "scanned_asset_name",
            "scanned_location",
            "scanned_location_name",
            "scanned_item",
            "scanned_item_name",
            "scanned_target",
            "notes",
            "photo_url",
            "photo_caption",
        ]
        read_only_fields = ["id", "scanned_at", "photo_url"]

    def get_photo_url(self, obj):
        if not obj.photo:
            return None
        request = self.context.get("request")
        if request is not None:
            return request.build_absolute_uri(obj.photo.url)
        return obj.photo.url


class ChecklistCompletionSerializer(serializers.ModelSerializer):
    """Serializer for checklist completion records."""

    checklist_name = serializers.CharField(source="checklist.name", read_only=True)
    user_username = serializers.CharField(source="user.username", read_only=True)
    step_completions = ChecklistStepCompletionSerializer(many=True, read_only=True)
    completed_steps_count = serializers.SerializerMethodField()
    total_steps_count = serializers.SerializerMethodField()
    required_steps_completed = serializers.SerializerMethodField()
    required_steps_total = serializers.SerializerMethodField()

    class Meta:
        model = ChecklistCompletion
        fields = [
            "id",
            "checklist",
            "checklist_name",
            "user",
            "user_username",
            "user_name",
            "started_at",
            "completed_at",
            "status",
            "step_completions",
            "completed_steps_count",
            "total_steps_count",
            "required_steps_completed",
            "required_steps_total",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "started_at",
            "completed_at",
            "status",
            "created_at",
            "updated_at",
        ]

    def get_completed_steps_count(self, obj):
        """Get the number of completed steps."""
        return obj.step_completions.count()

    def get_total_steps_count(self, obj):
        """Get the total number of steps in the checklist."""
        return obj.checklist.steps.count()

    def get_required_steps_completed(self, obj):
        """Get the number of required steps that have been completed."""
        completed_step_ids = set(obj.step_completions.values_list("step_id", flat=True))
        required_steps = obj.checklist.steps.filter(required=True)
        return sum(1 for step in required_steps if step.id in completed_step_ids)

    def get_required_steps_total(self, obj):
        """Get the total number of required steps."""
        return obj.checklist.steps.filter(required=True).count()


class ChecklistCompletionCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating a new checklist completion."""

    class Meta:
        model = ChecklistCompletion
        fields = ["checklist", "user_name"]
        extra_kwargs = {"user_name": {"required": False, "allow_blank": True}}


class ChecklistStepScanSerializer(serializers.Serializer):
    """Serializer for scanning a step."""

    step_id = serializers.UUIDField()
    asset_id = serializers.UUIDField(required=False, allow_null=True)
    location_id = serializers.IntegerField(required=False, allow_null=True)
    item_id = serializers.UUIDField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    photo = serializers.ImageField(required=False, allow_null=True)
    photo_caption = serializers.CharField(required=False, allow_blank=True, max_length=500)

    def validate(self, data):
        """Validate that exactly one of asset_id, location_id, or item_id is provided."""
        # Same exactly-one primitive as TypedTargetModel.clean() (#884), so the
        # scan-input rule and the model rule can't drift apart.
        count = count_present_targets(
            [data.get("asset_id"), data.get("location_id"), data.get("item_id")]
        )
        if count != 1:
            raise serializers.ValidationError(
                "Exactly one of asset_id, location_id, or item_id must be provided."
            )
        return data
