"""
Serializers for inventory API.
"""

from rest_framework import serializers

from .models import (
    Asset,
    AssetPart,
    AssetProblem,
    Category,
    Fixture,
    FixtureRefillRequest,
    InventoryItem,
    ItemSupplier,
    PriceHistory,
    Supplier,
    UsageLog,
)


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = "__all__"


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = "__all__"


class UsageLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = UsageLog
        fields = "__all__"
        read_only_fields = ["usage_date"]


class PriceHistorySerializer(serializers.ModelSerializer):
    """Serializer for price history records."""

    item_name = serializers.CharField(source="item_supplier.item.name", read_only=True)
    supplier_name = serializers.CharField(source="item_supplier.supplier.name", read_only=True)
    price_change_percentage = serializers.DecimalField(
        max_digits=6, decimal_places=2, read_only=True
    )

    class Meta:
        model = PriceHistory
        fields = [
            "id",
            "item_name",
            "supplier_name",
            "unit_cost",
            "package_cost",
            "quantity_per_package",
            "change_type",
            "recorded_at",
            "notes",
            "price_change_percentage",
        ]
        read_only_fields = ["recorded_at", "price_change_percentage"]


class ItemSupplierSerializer(serializers.ModelSerializer):
    """Serializer for item-supplier relationships with pricing and dimensional data."""

    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)
    # REMOVED: recent_price_history to prevent circular recursion
    # Use ItemSupplierDetailSerializer for full details including price history

    # Calculated dimensional properties
    package_volume = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    unit_weight = serializers.DecimalField(max_digits=8, decimal_places=3, read_only=True)
    package_dimensions_display = serializers.CharField(read_only=True)

    class Meta:
        model = ItemSupplier
        fields = [
            "id",
            "item",
            "item_name",
            "supplier",
            "supplier_name",
            "supplier_sku",
            "supplier_url",
            "package_upc",
            "unit_upc",
            "quantity_per_package",
            # Dimensional fields
            "package_height",
            "package_width",
            "package_length",
            "package_weight",
            # Calculated dimensional properties
            "package_volume",
            "unit_weight",
            "package_dimensions_display",
            # Pricing
            "unit_cost",
            "package_cost",
            "average_lead_time",
            "is_primary",
            "is_active",
            "is_discontinued",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]


class ItemSupplierDetailSerializer(ItemSupplierSerializer):
    """
    Extended serializer with price history.
    Use this for detail views where full supplier information is needed.
    """

    recent_price_history = PriceHistorySerializer(source="price_history", many=True, read_only=True)

    class Meta(ItemSupplierSerializer.Meta):
        fields = ItemSupplierSerializer.Meta.fields + ["recent_price_history"]

    def to_representation(self, instance):
        """Limit price history to recent records for performance."""
        data = super().to_representation(instance)
        # Limit to most recent 10 price history records
        if "recent_price_history" in data:
            data["recent_price_history"] = data["recent_price_history"][:10]
        return data


class InventoryItemSerializer(serializers.ModelSerializer):
    # Primary supplier fields (for backward compatibility)
    supplier_name = serializers.SerializerMethodField()
    category_name = serializers.CharField(source="category.name", read_only=True)

    def get_supplier_name(self, obj):
        """Safely get supplier name, handling None values."""
        supplier = obj.supplier if hasattr(obj, "supplier") else None
        return supplier.name if supplier else None

    needs_reorder = serializers.BooleanField(read_only=True)
    total_value = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    image = serializers.ImageField(read_only=True)
    thumbnail = serializers.SerializerMethodField()
    qr_code_url = serializers.SerializerMethodField()
    location = serializers.CharField(source="location.name", read_only=True)

    # Complete supplier information array
    suppliers = ItemSupplierSerializer(source="item_suppliers", many=True, read_only=True)

    # Reorder status and tracking fields
    reorder_status = serializers.CharField(read_only=True)
    has_pending_reorder = serializers.BooleanField(read_only=True)
    expected_delivery_date = serializers.DateField(
        source="get_expected_delivery_date", read_only=True
    )
    active_reorder_request = serializers.SerializerMethodField()

    # Case-based reordering fields
    current_cases = serializers.FloatField(read_only=True)

    # Hazmat calculated fields
    nfpa_fire_diamond_display = serializers.ReadOnlyField()
    hazmat_compliance_status = serializers.ReadOnlyField()
    has_complete_nfpa_data = serializers.ReadOnlyField()
    msds_file_url = serializers.SerializerMethodField()

    class Meta:
        model = InventoryItem
        fields = [
            "id",
            "name",
            "description",
            "sku",
            "image",
            "thumbnail",
            "qr_code_url",
            "category",
            "category_name",
            "location",
            "reorder_quantity",
            "current_stock",
            "minimum_stock",
            # Case-based reordering fields
            "use_case_based_reorder",
            "minimum_cases",
            "reorder_cases",
            "current_cases",
            "reorder_instruction",
            "supplier_name",
            "supplier_sku",
            "supplier_url",
            "unit_cost",
            "package_cost",
            "quantity_per_package",
            "average_lead_time",
            "qr_code",
            # Complete supplier array with all details
            "suppliers",
            # Reorder status and tracking
            "reorder_status",
            "has_pending_reorder",
            "expected_delivery_date",
            "active_reorder_request",
            # Hazmat fields
            "is_hazardous",
            "msds_url",
            "msds_file_url",
            "nfpa_health_hazard",
            "nfpa_fire_hazard",
            "nfpa_instability_hazard",
            "nfpa_special_hazards",
            "nfpa_fire_diamond_display",
            "hazmat_compliance_status",
            "has_complete_nfpa_data",
            "is_active",
            "is_requestable",
            "last_scanned_at",
            "notes",
            "needs_reorder",
            "total_value",
            # Ownership fields
            "ownership_type",
            "owning_user",
            "owning_group",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["qr_code", "created_at", "updated_at"]

    def get_thumbnail(self, obj):
        """Return the thumbnail URL when available."""
        try:
            if obj.thumbnail:
                request = self.context.get("request")
                if request:
                    return request.build_absolute_uri(obj.thumbnail.url)
                return obj.thumbnail.url
            return None
        except Exception:
            return None

    def get_qr_code_url(self, obj):
        """Return the QR code URL when available."""

        try:
            return obj.qr_code.url if obj.qr_code else None
        except Exception:
            return None

    def get_msds_file_url(self, obj):
        """Return the MSDS file URL when available."""
        try:
            return obj.msds_file.url if obj.msds_file else None
        except Exception:
            return None

    def get_active_reorder_request(self, obj):
        """Return details of the active reorder request if any."""
        active_request = obj.get_active_reorder_request()
        if active_request:
            return {
                "id": active_request.id,
                "status": active_request.status,
                "quantity": active_request.quantity,
                "requested_at": active_request.requested_at,
                "ordered_at": active_request.ordered_at,
                "requested_by": active_request.requested_by,
                "priority": active_request.priority,
                # Review/approval information
                "reviewed_by": (
                    active_request.reviewed_by.username if active_request.reviewed_by else None
                ),
                "reviewed_at": active_request.reviewed_at,
            }
        return None


class InventoryItemDetailSerializer(InventoryItemSerializer):
    """Extended serializer with related data and full supplier details including price history."""

    recent_usage = UsageLogSerializer(source="usage_logs", many=True, read_only=True)
    supplier_details = SupplierSerializer(source="supplier", read_only=True)
    category_details = CategorySerializer(source="category", read_only=True)
    all_suppliers = ItemSupplierDetailSerializer(source="item_suppliers", many=True, read_only=True)
    price_trend_summary = serializers.SerializerMethodField()

    class Meta(InventoryItemSerializer.Meta):
        fields = InventoryItemSerializer.Meta.fields + [
            "recent_usage",
            "supplier_details",
            "category_details",
            "all_suppliers",
            "price_trend_summary",
        ]

    def get_price_trend_summary(self, obj):
        """Get price trend summary for the primary supplier."""
        primary_supplier = obj.primary_item_supplier
        if not primary_supplier:
            return None

        # Get recent price history (last 5 records)
        recent_history = primary_supplier.price_history.all()[:5]
        if len(recent_history) < 2:
            return {"trend": "insufficient_data", "change_percentage": None}

        latest = recent_history[0]
        previous = recent_history[1]

        if latest.unit_cost and previous.unit_cost:
            change_percentage = latest.price_change_percentage
            if change_percentage is None:
                return {"trend": "no_change", "change_percentage": 0}
            elif change_percentage > 0:
                trend = "increasing"
            elif change_percentage < 0:
                trend = "decreasing"
            else:
                trend = "stable"

            return {
                "trend": trend,
                "change_percentage": change_percentage,
                "latest_cost": latest.unit_cost,
                "previous_cost": previous.unit_cost,
                "last_updated": latest.recorded_at,
            }

        return {"trend": "no_data", "change_percentage": None}


class AssetPartSerializer(serializers.ModelSerializer):
    """Serializer for asset parts/consumables."""

    part_name = serializers.CharField(source="part.name", read_only=True)
    part_sku = serializers.CharField(source="part.sku", read_only=True)
    asset_name = serializers.CharField(source="asset.name", read_only=True)
    asset_tag = serializers.CharField(source="asset.asset_tag", read_only=True)

    # Calculated properties
    days_since_replacement = serializers.ReadOnlyField()
    needs_replacement = serializers.ReadOnlyField()

    # Part details (nested)
    part_details = serializers.SerializerMethodField()

    class Meta:
        model = AssetPart
        fields = [
            "id",
            "asset",
            "asset_name",
            "asset_tag",
            "part",
            "part_name",
            "part_sku",
            "quantity_needed",
            "is_required",
            "maintenance_interval_days",
            "last_replaced_at",
            "days_since_replacement",
            "needs_replacement",
            "notes",
            "part_details",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "days_since_replacement",
            "needs_replacement",
            "created_at",
            "updated_at",
        ]

    def get_part_details(self, obj):
        """Return basic details about the part inventory item."""
        part = obj.part
        return {
            "id": str(part.id),
            "name": part.name,
            "sku": part.sku,
            "current_stock": part.current_stock,
            "minimum_stock": part.minimum_stock,
            "needs_reorder": part.needs_reorder,
            "category_name": part.category.name if part.category else None,
        }


class AssetSerializer(serializers.ModelSerializer):
    """Serializer for hard asset tracking."""

    # Related field names for display
    inventory_item_name = serializers.CharField(source="inventory_item.name", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)
    location_name = serializers.CharField(source="location.name", read_only=True)
    manufacturer_name_display = serializers.CharField(source="manufacturer.name", read_only=True)

    # Calculated properties
    display_manufacturer = serializers.ReadOnlyField()
    acquisition_display = serializers.ReadOnlyField()
    age_in_days = serializers.ReadOnlyField()

    # Image/file URLs
    image_url = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()
    qr_code_url = serializers.SerializerMethodField()
    qr_code_scan_url = serializers.SerializerMethodField()
    manual_pdf_url = serializers.SerializerMethodField()

    # ForgeKey-related fields (from forgekey app)
    operational_mode = serializers.SerializerMethodField()
    is_locked = serializers.SerializerMethodField()
    lockout_info = serializers.SerializerMethodField()
    owning_group_name = serializers.SerializerMethodField()
    owning_user_name = serializers.SerializerMethodField()

    # Authorization fields
    can_enable = serializers.SerializerMethodField()
    can_unlock = serializers.SerializerMethodField()

    # Parts/consumables
    parts = AssetPartSerializer(source="asset_parts", many=True, read_only=True)

    class Meta:
        model = Asset
        fields = [
            "id",
            "name",
            "description",
            "serial_number",
            "asset_tag",
            # Relationships
            "inventory_item",
            "inventory_item_name",
            "category",
            "category_name",
            "location",
            "location_name",
            # Manufacturer
            "manufacturer",
            "manufacturer_name",
            "manufacturer_name_display",
            "display_manufacturer",
            # Acquisition
            "date_received",
            "amount_paid",
            "is_donation",
            "donor_name",
            "acquisition_display",
            "age_in_days",
            # Product info
            "product_url",
            "wiki_page_url",
            # Maintenance
            "maintenance_plan",
            # Parts/consumables
            "parts",
            # Operational requirements
            "circuit",
            "needs_compressed_air",
            "needs_ventilation",
            "is_chargeable",
            # Scanning tracking
            "last_scanned_at",
            # Group ownership
            "owning_group",
            "owning_group_name",
            "owning_user_name",
            "groups_can_enable",
            # ForgeKey fields (operational mode and lockout status)
            "operational_mode",
            "is_locked",
            "lockout_info",
            # Authorization
            "can_enable",
            "can_unlock",
            # Media
            "image",
            "image_url",
            "thumbnail_url",
            "manual_pdf",
            "manual_pdf_url",
            "qr_code",
            "qr_code_url",
            "qr_code_scan_url",
            # Status
            "status",
            "condition_notes",
            # Metadata
            "is_active",
            "report_only",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "asset_tag",
            "qr_code",
            "last_scanned_at",
            "operational_mode",
            "is_locked",
            "lockout_info",
            "owning_group_name",
            "owning_user_name",
            "can_enable",
            "can_unlock",
            "qr_code_url",
            "qr_code_scan_url",
            "created_at",
            "updated_at",
        ]

    def get_operational_mode(self, obj):
        """Get operational mode from forgekey app."""
        try:
            from forgekey.models import OperationalMode

            mode = OperationalMode.objects.get(asset=obj)
            return {
                "mode": mode.mode,
                "classroom_mode_enabled": mode.classroom_mode_enabled,
            }
        except OperationalMode.DoesNotExist:
            return {"mode": "available", "classroom_mode_enabled": False}

    def get_is_locked(self, obj):
        """Check if asset is locked via forgekey lockouts."""
        try:
            from forgekey.models import DeviceLockout

            return DeviceLockout.objects.filter(asset=obj, is_active=True).exists()
        except Exception:
            return False

    def get_lockout_info(self, obj):
        """Get lockout information from forgekey app."""
        try:
            from forgekey.models import DeviceLockout

            active_lockout = DeviceLockout.objects.filter(asset=obj, is_active=True).first()
            if active_lockout:
                return {
                    "locked_by": (
                        active_lockout.locked_by.username if active_lockout.locked_by else None
                    ),
                    "locked_at": (
                        active_lockout.locked_at.isoformat() if active_lockout.locked_at else None
                    ),
                    "lockout_level": active_lockout.lockout_level,
                    "reason": active_lockout.reason,
                }
            return None
        except Exception:
            return None

    def get_image_url(self, obj):
        """Return the image URL when available."""
        try:
            return obj.image.url if obj.image else None
        except Exception:
            return None

    def get_thumbnail_url(self, obj):
        """Return the thumbnail URL when available."""
        try:
            if obj.thumbnail:
                request = self.context.get("request")
                if request:
                    return request.build_absolute_uri(obj.thumbnail.url)
                return obj.thumbnail.url
            return None
        except Exception:
            return None

    def get_qr_code_url(self, obj):
        """Return the QR code image URL when available."""
        try:
            return obj.qr_code.url if obj.qr_code else None
        except Exception:
            return None

    def get_qr_code_scan_url(self, obj):
        """Return the scan URL that the QR code points to."""
        from django.conf import settings

        frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
        return f"{frontend_url}/scan/asset/{obj.id}"

    def get_owning_group_name(self, obj):
        """Return owning group name, or 'Logistics' if owned by space."""
        if obj.ownership_type == obj.OWNERSHIP_TYPE_SPACE:
            return "Logistics"
        if obj.owning_group:
            return obj.owning_group.name
        return None

    def get_owning_user_name(self, obj):
        """Return owning user name, or 'COO' if owned by space."""
        if obj.ownership_type == obj.OWNERSHIP_TYPE_SPACE:
            return "COO"
        if obj.owning_user:
            return obj.owning_user.username
        return None

    def get_can_enable(self, obj):
        """Check if the current user can enable this asset."""
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False

        # Report-only assets cannot be enabled
        if obj.report_only:
            return False

        user = request.user

        # Admins can always enable
        if obj.is_user_admin(user):
            return True

        # Check if user can operate assets in Implementing/Testing status
        if obj.status in [obj.IMPLEMENTING, obj.TESTING]:
            return obj.can_user_operate(user)

        # Check if user's groups are in groups_can_enable
        user_groups = user.groups.all()
        if obj.groups_can_enable.exists():
            return any(group in obj.groups_can_enable.all() for group in user_groups)

        # If no groups specified, default to allowing (for backward compatibility)
        return True

    def get_can_unlock(self, obj):
        """Check if the current user can lock or unlock this asset."""
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False

        # Report-only assets cannot be locked/unlocked
        if obj.report_only:
            return False

        user = request.user

        # Admins can always lock/unlock
        if obj.is_user_admin(user):
            return True

        # Check if asset is locked
        try:
            from forgekey.models import DeviceLockout

            active_lockouts = DeviceLockout.objects.filter(asset=obj, is_active=True)

            if active_lockouts.exists():
                # Check if user can unlock any of the lockouts
                for lockout in active_lockouts:
                    if lockout.can_be_unlocked_by(user):
                        return True
                return False
            else:
                # Asset is not locked - check if user can lock it
                # For now, allow locking if user is in logistics or has group permissions
                # This can be customized based on your requirements
                if obj.is_user_in_logistics(user):
                    return True
                # Check if user is in a group that can enable this asset
                user_groups = user.groups.all()
                if obj.groups_can_enable.exists():
                    return any(group in obj.groups_can_enable.all() for group in user_groups)
                return False
        except Exception:
            return False

    def get_manual_pdf_url(self, obj):
        """Return the manual PDF URL when available."""
        try:
            return obj.manual_pdf.url if obj.manual_pdf else None
        except Exception:
            return None


class AssetProblemSerializer(serializers.ModelSerializer):
    """Serializer for asset problem reports."""

    asset_name = serializers.CharField(source="asset.name", read_only=True)
    asset_tag = serializers.CharField(source="asset.asset_tag", read_only=True)

    class Meta:
        model = AssetProblem
        fields = [
            "id",
            "asset",
            "asset_name",
            "asset_tag",
            "reported_by",
            "description",
            "status",
            "resolution_notes",
            "created_at",
            "updated_at",
            "resolved_at",
        ]
        read_only_fields = ["created_at", "updated_at", "resolved_at"]


class FixtureSerializer(serializers.ModelSerializer):
    """Serializer for fixtures (refillable assets)."""

    # Display names for related fields
    location_name = serializers.CharField(source="location.name", read_only=True)
    refill_item_name = serializers.CharField(source="refill_item.name", read_only=True)
    refill_item_sku = serializers.CharField(source="refill_item.sku", read_only=True)

    # Calculated fields
    pending_requests_count = serializers.ReadOnlyField()

    # QR code URL
    qr_code_url = serializers.SerializerMethodField()

    class Meta:
        model = Fixture
        fields = [
            "id",
            "name",
            "description",
            "location",
            "location_name",
            "refill_item",
            "refill_item_name",
            "refill_item_sku",
            "asset_tag",
            "is_active",
            "pending_requests_count",
            "qr_code_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def get_qr_code_url(self, obj):
        """Generate QR code URL for fixture scanning."""
        from django.conf import settings

        base_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
        return f"{base_url.rstrip('/')}/scan/fixture/{obj.id}"


class FixtureRefillRequestSerializer(serializers.ModelSerializer):
    """Serializer for fixture refill requests."""

    # Display names for related fields
    fixture_name = serializers.CharField(source="fixture.name", read_only=True)
    fixture_location = serializers.CharField(source="fixture.location.name", read_only=True)
    refill_item_name = serializers.CharField(source="fixture.refill_item.name", read_only=True)
    refill_item_sku = serializers.CharField(source="fixture.refill_item.sku", read_only=True)

    # Calculated fields
    time_to_resolve = serializers.ReadOnlyField()

    class Meta:
        model = FixtureRefillRequest
        fields = [
            "id",
            "fixture",
            "fixture_name",
            "fixture_location",
            "refill_item_name",
            "refill_item_sku",
            "status",
            "requested_at",
            "requested_by",
            "resolved_at",
            "resolved_by",
            "notes",
            "time_to_resolve",
        ]
        read_only_fields = ["requested_at", "resolved_at", "time_to_resolve"]


class FixtureDetailSerializer(FixtureSerializer):
    """Extended fixture serializer with recent refill requests."""

    recent_refill_requests = FixtureRefillRequestSerializer(
        source="refill_requests", many=True, read_only=True
    )
    refill_item_details = serializers.SerializerMethodField()

    class Meta(FixtureSerializer.Meta):
        fields = FixtureSerializer.Meta.fields + [
            "recent_refill_requests",
            "refill_item_details",
        ]

    def get_refill_item_details(self, obj):
        """Return basic details about the refill inventory item."""
        item = obj.refill_item
        return {
            "id": item.id,
            "name": item.name,
            "sku": item.sku,
            "current_stock": item.current_stock,
            "minimum_stock": item.minimum_stock,
            "needs_reorder": item.needs_reorder,
        }

    def to_representation(self, instance):
        """Limit recent requests to last 10."""
        data = super().to_representation(instance)
        if "recent_refill_requests" in data:
            data["recent_refill_requests"] = data["recent_refill_requests"][:10]
        return data
