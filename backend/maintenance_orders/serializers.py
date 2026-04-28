"""Serializers for the third-party maintenance work order API."""

from rest_framework import serializers

from .models import (
    AssetWarranty,
    ThirdPartyWorkOrder,
    ThirdPartyWorkOrderAsset,
    ThirdPartyWorkOrderAttachment,
)


class AssetWarrantySerializer(serializers.ModelSerializer):
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = AssetWarranty
        fields = [
            "id",
            "asset",
            "install_date",
            "duration_days",
            "end_date",
            "provider",
            "policy_number",
            "notes",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "is_active", "created_at", "updated_at"]

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        install_date = attrs.get("install_date", getattr(instance, "install_date", None))
        duration_days = attrs.get("duration_days", getattr(instance, "duration_days", None))
        end_date = attrs.get("end_date", getattr(instance, "end_date", None))
        if not duration_days and not end_date:
            raise serializers.ValidationError(
                "Provide either duration_days or end_date — one is required."
            )
        if duration_days and end_date and install_date:
            from datetime import timedelta

            if install_date + timedelta(days=duration_days) != end_date:
                raise serializers.ValidationError(
                    "duration_days and end_date disagree; provide only one."
                )
        return attrs


class ThirdPartyWorkOrderAssetSerializer(serializers.ModelSerializer):
    """Per-asset cost-share row for multi-asset work orders."""

    asset_name = serializers.CharField(source="asset.name", read_only=True)
    asset_tag = serializers.CharField(source="asset.asset_tag", read_only=True)

    class Meta:
        model = ThirdPartyWorkOrderAsset
        fields = [
            "id",
            "work_order",
            "asset",
            "asset_name",
            "asset_tag",
            "share_pct",
            "notes",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class ThirdPartyWorkOrderAttachmentSerializer(serializers.ModelSerializer):
    """Invoice / FSR / photo / quote / paper-form attachment."""

    kind_display = serializers.CharField(source="get_kind_display", read_only=True)
    uploaded_by_username = serializers.CharField(
        source="uploaded_by.username", read_only=True, allow_null=True
    )

    class Meta:
        model = ThirdPartyWorkOrderAttachment
        fields = [
            "id",
            "work_order",
            "file",
            "kind",
            "kind_display",
            "caption",
            "uploaded_by",
            "uploaded_by_username",
            "uploaded_at",
        ]
        read_only_fields = ["id", "uploaded_at", "uploaded_by_username"]


class ThirdPartyWorkOrderSerializer(serializers.ModelSerializer):
    """Full third-party work order with nested asset distribution + attachments."""

    short_id = serializers.CharField(read_only=True)
    work_type_display = serializers.CharField(source="get_work_type_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    vendor_name = serializers.CharField(source="vendor.name", read_only=True)
    asset_name = serializers.CharField(source="asset.name", read_only=True, allow_null=True)
    asset_links = ThirdPartyWorkOrderAssetSerializer(many=True, read_only=True)
    attachments = ThirdPartyWorkOrderAttachmentSerializer(many=True, read_only=True)
    active_warranty = serializers.SerializerMethodField()

    def get_active_warranty(self, obj):
        from .signals import active_warranty_for

        warranty = active_warranty_for(obj.asset)
        if warranty is None:
            return None
        return AssetWarrantySerializer(warranty).data

    class Meta:
        model = ThirdPartyWorkOrder
        fields = [
            "id",
            "short_id",
            "title",
            "asset",
            "asset_name",
            "vendor",
            "vendor_name",
            "work_type",
            "work_type_display",
            "is_emergency",
            "status",
            "status_display",
            "nte_amount",
            "par_cost_buffer",
            "actual_invoice_total",
            "dispatch_fee",
            "downtime_start",
            "downtime_end",
            "keyfob_id",
            "warranty_recovery",
            "shadow_user",
            "opened_by",
            "opened_at",
            "closed_at",
            "notes",
            "internal_notes",
            "asset_links",
            "attachments",
            "active_warranty",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "short_id", "opened_at", "created_at", "updated_at"]

    def validate(self, attrs):
        """Enforce SIG-work asset linkage rule.

        Standard / major-repair / buildout work MUST target an asset (the
        bead's "All standard work orders MUST be linked to an Asset ID" rule).
        Building Emergency dispatch is allowed without an asset. The state
        machine that enforces this transition will land in Phase 3; for now
        we just validate the basic invariant at write time.
        """
        # On partial update, fall back to the instance's current values.
        instance = getattr(self, "instance", None)
        work_type = attrs.get(
            "work_type", getattr(instance, "work_type", ThirdPartyWorkOrder.WORK_TYPE_STANDARD)
        )
        is_emergency = attrs.get("is_emergency", getattr(instance, "is_emergency", False))
        asset = attrs.get("asset", getattr(instance, "asset", None))

        if (
            work_type != ThirdPartyWorkOrder.WORK_TYPE_BUILDING_EMERGENCY
            and not is_emergency
            and asset is None
        ):
            raise serializers.ValidationError(
                {"asset": "Asset is required for non-emergency work orders."}
            )
        return attrs
