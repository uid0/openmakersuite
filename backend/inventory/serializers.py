"""
Serializers for inventory API.
"""

from rest_framework import serializers

from .models import (
    Asset,
    AssetOutOfService,
    AssetPart,
    AssetProblem,
    AssetProblemPhoto,
    AssetReservation,
    Category,
    Fixture,
    FixtureRefillRequest,
    InventoryItem,
    ItemSupplier,
    Location,
    LocationProblem,
    MaintenanceItem,
    MaintenanceLog,
    MaintenanceMaterial,
    MaintenanceRecord,
    MaintenanceTask,
    PriceHistory,
    StockReconciliation,
    Supplier,
    UsageLog,
    WorkOrder,
    WorkOrderMaterialUsage,
    WorkOrderPhoto,
    WorkOrderSubmission,
    WorkOrderTaskCompletion,
    WorkOrderValidation,
)


class SupplierSerializer(serializers.ModelSerializer):
    """Basic serializer for supplier list views."""

    item_count = serializers.SerializerMethodField()
    purchase_order_count = serializers.SerializerMethodField()
    total_spent = serializers.SerializerMethodField()

    class Meta:
        model = Supplier
        fields = "__all__"
        read_only_fields = ["created_at", "updated_at"]

    def get_item_count(self, obj):
        """Count of items supplied by this supplier."""
        return obj.supplier_items.filter(is_active=True).count()

    def get_purchase_order_count(self, obj):
        """Count of purchase orders with this supplier."""
        try:
            from reorder_queue.models import PurchaseOrder

            return PurchaseOrder.objects.filter(supplier=obj).count()
        except ImportError:
            return 0

    def get_total_spent(self, obj):
        """Sum of actual totals from received purchase orders."""
        try:
            from decimal import Decimal

            from django.db.models import Sum

            from reorder_queue.models import PurchaseOrder

            result = PurchaseOrder.objects.filter(
                supplier=obj, status=PurchaseOrder.RECEIVED, actual_total__isnull=False
            ).aggregate(total=Sum("actual_total"))["total"] or Decimal("0.00")
            return str(result)
        except (ImportError, TypeError):
            return "0.00"


class CategorySerializer(serializers.ModelSerializer):
    item_count = serializers.SerializerMethodField()
    parent_name = serializers.CharField(source="parent.name", read_only=True)
    children = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = "__all__"
        read_only_fields = ["slug"]

    def get_item_count(self, obj):
        """Count of items in this category."""
        return obj.items.filter(is_active=True).count()

    def get_children(self, obj):
        """Get child categories."""
        children = obj.children.all()
        return CategorySerializer(children, many=True).data


class LocationSerializer(serializers.ModelSerializer):
    parent_name = serializers.CharField(source="parent.name", read_only=True)
    fixture_count = serializers.SerializerMethodField()
    qr_code_url = serializers.SerializerMethodField()

    class Meta:
        model = Location
        fields = "__all__"
        read_only_fields = ["created_at", "updated_at", "access_code"]

    def get_fixture_count(self, obj):
        """Count of fixtures at this location."""
        return obj.fixtures.filter(is_active=True).count()

    def get_qr_code_url(self, obj):
        """Get QR code URL if available."""
        if obj.qr_code:
            return obj.qr_code.url
        return None


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


class SupplierDetailSerializer(SupplierSerializer):
    """Extended serializer for supplier detail views with related data."""

    items = ItemSupplierSerializer(source="supplier_items", many=True, read_only=True)
    purchase_orders = serializers.SerializerMethodField()
    lead_time_analytics = serializers.SerializerMethodField()
    price_trends = serializers.SerializerMethodField()

    class Meta(SupplierSerializer.Meta):
        fields = [
            "id",
            "name",
            "supplier_type",
            "website",
            "account_number",
            "tax_free_paperwork_filed",
            "notes",
            "created_at",
            "updated_at",
            "item_count",
            "purchase_order_count",
            "total_spent",
            "items",
            "purchase_orders",
            "lead_time_analytics",
            "price_trends",
        ]

    def get_purchase_orders(self, obj):
        """Get purchase orders for this supplier."""
        try:
            from reorder_queue.models import PurchaseOrder
            from reorder_queue.serializers import PurchaseOrderSerializer

            orders = PurchaseOrder.objects.filter(supplier=obj).order_by("-order_date")[:50]
            return PurchaseOrderSerializer(orders, many=True).data
        except ImportError:
            return []

    def get_lead_time_analytics(self, obj):
        """Get lead time analytics for this supplier."""
        try:
            from django.db.models import Avg, Count, Max, Min

            from reorder_queue.models import LeadTimeLog

            # Get all lead time logs for items from this supplier
            logs = LeadTimeLog.objects.filter(item_supplier__supplier=obj)

            if not logs.exists():
                return {
                    "average_lead_time": None,
                    "min_lead_time": None,
                    "max_lead_time": None,
                    "average_variance": None,
                    "total_orders": 0,
                    "on_time_percentage": None,
                }

            stats = logs.aggregate(
                avg_lead_time=Avg("actual_lead_time_days"),
                min_lead_time=Min("actual_lead_time_days"),
                max_lead_time=Max("actual_lead_time_days"),
                avg_variance=Avg("variance_days"),
                total_orders=Count("id"),
            )

            # Calculate on-time percentage
            on_time_count = logs.filter(variance_days__lte=0).count()
            on_time_percentage = (
                (on_time_count / stats["total_orders"] * 100) if stats["total_orders"] > 0 else None
            )

            return {
                "average_lead_time": (
                    float(stats["avg_lead_time"]) if stats["avg_lead_time"] else None
                ),
                "min_lead_time": stats["min_lead_time"],
                "max_lead_time": stats["max_lead_time"],
                "average_variance": (
                    float(stats["avg_variance"]) if stats["avg_variance"] else None
                ),
                "total_orders": stats["total_orders"],
                "on_time_percentage": (
                    float(on_time_percentage) if on_time_percentage is not None else None
                ),
                "recent_logs": [
                    {
                        "item_name": log.item_supplier.item.name,
                        "order_date": log.order_date.isoformat(),
                        "expected_delivery_date": log.expected_delivery_date.isoformat(),
                        "actual_delivery_date": log.actual_delivery_date.isoformat(),
                        "estimated_lead_time_days": log.estimated_lead_time_days,
                        "actual_lead_time_days": log.actual_lead_time_days,
                        "variance_days": log.variance_days,
                        "was_late": log.was_late,
                    }
                    for log in logs.order_by("-actual_delivery_date")[:10]
                ],
            }
        except ImportError:
            return {}

    def get_price_trends(self, obj):
        """Get price trends for items from this supplier."""
        try:
            from datetime import timedelta

            from django.utils import timezone

            # Get price history for items from this supplier
            price_history = PriceHistory.objects.filter(item_supplier__supplier=obj).order_by(
                "-recorded_at"
            )

            if not price_history.exists():
                return {
                    "trends": [],
                    "summary": {
                        "average_unit_cost": None,
                        "min_unit_cost": None,
                        "max_unit_cost": None,
                        "price_changes_count": 0,
                    },
                }

            # Get recent price changes (last 6 months)
            six_months_ago = timezone.now() - timedelta(days=180)
            recent_history = price_history.filter(recorded_at__gte=six_months_ago)

            # Group by item and get trends
            trends = []
            items_seen = set()

            for price_record in recent_history[:50]:  # Limit to 50 most recent
                item_supplier = price_record.item_supplier
                item_id = str(item_supplier.item.id)

                if item_id not in items_seen:
                    items_seen.add(item_id)
                    # Get all price history for this item-supplier
                    item_history = PriceHistory.objects.filter(
                        item_supplier=item_supplier
                    ).order_by("recorded_at")[:20]

                    trends.append(
                        {
                            "item_id": item_id,
                            "item_name": item_supplier.item.name,
                            "price_history": [
                                {
                                    "recorded_at": ph.recorded_at.isoformat(),
                                    "unit_cost": (float(ph.unit_cost) if ph.unit_cost else None),
                                    "package_cost": (
                                        float(ph.package_cost) if ph.package_cost else None
                                    ),
                                    "change_type": ph.change_type,
                                    "price_change_percentage": (
                                        float(ph.price_change_percentage)
                                        if ph.price_change_percentage
                                        else None
                                    ),
                                }
                                for ph in item_history
                            ],
                        }
                    )

            # Calculate summary statistics
            unit_costs = [float(ph.unit_cost) for ph in price_history if ph.unit_cost is not None]

            return {
                "trends": trends,
                "summary": {
                    "average_unit_cost": (
                        sum(unit_costs) / len(unit_costs) if unit_costs else None
                    ),
                    "min_unit_cost": min(unit_costs) if unit_costs else None,
                    "max_unit_cost": max(unit_costs) if unit_costs else None,
                    "price_changes_count": price_history.count(),
                },
            }
        except Exception:
            return {"trends": [], "summary": {}}


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

    # Power / electrical computed flag
    is_forgekey_managed = serializers.ReadOnlyField()

    # Read-only summary of the asset's breaker + disconnect; writes still
    # go through the dedicated FK fields.
    breaker_summary = serializers.SerializerMethodField()
    disconnect_summary = serializers.SerializerMethodField()

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
            # NOTE: maintenance_plan is a legacy free-text field kept on the
            # model + admin form for back-compat. The asset detail page no
            # longer renders it — scheduled maintenance lives on
            # MaintenanceItem and unscheduled work on WorkOrder. See oms-4mk.
            "maintenance_plan",
            # Parts/consumables
            "parts",
            # Operational requirements
            "circuit",
            "needs_compressed_air",
            "needs_ventilation",
            "is_chargeable",
            "mac_address",
            # Power / electrical
            "breaker",
            "breaker_summary",
            "disconnect",
            "disconnect_summary",
            "power_draw_watts",
            "wiring_type",
            "suite",
            "electrical_box",
            "breaker_location",
            "has_interlock",
            "interlock_type",
            "interlock_responsible",
            "lockout_type",
            "lockout_instructions",
            "lockout_responsible",
            "has_network_drop",
            "network_drop_location",
            "is_forgekey_managed",
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
            # NOTE: condition_notes is a legacy free-text field kept on the
            # model + admin form for back-compat. The asset detail page no
            # longer renders it — current condition is reflected by
            # AssetProblem (Problem History) and WorkOrder. See oms-4mk.
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
            "is_forgekey_managed",
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

    def get_breaker_summary(self, obj):
        if obj.breaker_id is None:
            return None
        b = obj.breaker
        panel = b.panel
        return {
            "id": b.pk,
            "panel_id": panel.pk if panel else None,
            "panel_name": panel.name if panel else "",
            "position": b.position,
            "amperage": b.amperage,
            "label": b.label,
        }

    def get_disconnect_summary(self, obj):
        if obj.disconnect_id is None:
            return None
        d = obj.disconnect
        return {
            "id": d.pk,
            "label": d.label,
            "disconnect_type": d.disconnect_type,
            "is_lockable": d.is_lockable,
        }


class AssetProblemPhotoSerializer(serializers.ModelSerializer):
    """Serializer for photos attached to an asset problem report."""

    uploaded_by_name = serializers.SerializerMethodField()
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = AssetProblemPhoto
        fields = [
            "id",
            "problem",
            "image",
            "image_url",
            "caption",
            "uploaded_by",
            "uploaded_by_name",
            "uploaded_at",
        ]
        read_only_fields = [
            "problem",
            "uploaded_at",
            "uploaded_by",
            "uploaded_by_name",
            "image_url",
        ]

    def get_uploaded_by_name(self, obj):
        if obj.uploaded_by:
            return obj.uploaded_by.get_full_name() or obj.uploaded_by.username
        return None

    def get_image_url(self, obj):
        request = self.context.get("request")
        if obj.image and request:
            return request.build_absolute_uri(obj.image.url)
        if obj.image:
            return obj.image.url
        return None


class AssetProblemSerializer(serializers.ModelSerializer):
    """Serializer for asset problem reports."""

    asset_name = serializers.CharField(source="asset.name", read_only=True)
    asset_tag = serializers.CharField(source="asset.asset_tag", read_only=True)
    photos = AssetProblemPhotoSerializer(many=True, read_only=True)

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
            "resolved_by",
            "photos",
        ]
        read_only_fields = ["created_at", "updated_at", "resolved_at"]


class LocationProblemSerializer(serializers.ModelSerializer):
    """Serializer for location problem reports."""

    location_name = serializers.CharField(source="location.name", read_only=True)
    photo_url = serializers.SerializerMethodField()
    paper_form_url = serializers.SerializerMethodField()
    work_order_short_id = serializers.SerializerMethodField()
    third_party_work_order_short_id = serializers.SerializerMethodField()
    severity_display = serializers.CharField(source="get_severity_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = LocationProblem
        fields = [
            "id",
            "location",
            "location_name",
            "reported_by",
            "description",
            "status",
            "status_display",
            "severity",
            "severity_display",
            "photo",
            "photo_url",
            "paper_form_attachment",
            "paper_form_url",
            "work_order",
            "work_order_short_id",
            "third_party_work_order",
            "third_party_work_order_short_id",
            "resolution_notes",
            "reported_at",
            "updated_at",
            "resolved_at",
            "resolved_by",
        ]
        read_only_fields = [
            "id",
            "reported_at",
            "updated_at",
            "resolved_at",
            "work_order",
            "third_party_work_order",
        ]

    def _absolute(self, file_field):
        if not file_field:
            return None
        request = self.context.get("request")
        try:
            url = file_field.url
        except Exception:
            return None
        if request:
            return request.build_absolute_uri(url)
        return url

    def get_photo_url(self, obj):
        return self._absolute(obj.photo)

    def get_paper_form_url(self, obj):
        return self._absolute(obj.paper_form_attachment)

    def get_work_order_short_id(self, obj):
        return obj.work_order.short_id if obj.work_order else None

    def get_third_party_work_order_short_id(self, obj):
        return obj.third_party_work_order.short_id if obj.third_party_work_order else None


class MaintenanceMaterialSerializer(serializers.ModelSerializer):
    """Serializer for materials needed for a maintenance task."""

    total_estimated_cost = serializers.ReadOnlyField()
    inventory_item_detail = serializers.SerializerMethodField()

    class Meta:
        model = MaintenanceMaterial
        fields = [
            "id",
            "maintenance_item",
            "inventory_item",
            "inventory_item_detail",
            "name",
            "quantity",
            "unit",
            "estimated_cost_per_unit",
            "total_estimated_cost",
            "notes",
            "created_at",
        ]
        read_only_fields = ["total_estimated_cost", "inventory_item_detail", "created_at"]

    def get_inventory_item_detail(self, obj):
        item = obj.inventory_item
        if item is None:
            return None
        return {
            "id": str(item.id),
            "name": item.name,
            "current_stock": item.current_stock,
            "minimum_stock": item.minimum_stock,
            "reorder_quantity": item.reorder_quantity,
        }


class MaintenanceTaskSerializer(serializers.ModelSerializer):
    """Serializer for ordered sub-task steps within a maintenance item."""

    class Meta:
        model = MaintenanceTask
        fields = [
            "id",
            "maintenance_item",
            "order",
            "title",
            "description",
            "is_required",
            "created_at",
        ]
        read_only_fields = ["created_at"]


class MaintenanceItemSerializer(serializers.ModelSerializer):
    """Serializer for preventive maintenance tasks associated with an asset."""

    asset_name = serializers.CharField(source="asset.name", read_only=True)
    asset_tag = serializers.CharField(source="asset.asset_tag", read_only=True)
    materials = MaintenanceMaterialSerializer(many=True, read_only=True)
    tasks = MaintenanceTaskSerializer(many=True, read_only=True)
    is_overdue = serializers.ReadOnlyField()
    days_overdue = serializers.ReadOnlyField()
    next_due_at = serializers.ReadOnlyField()

    class Meta:
        model = MaintenanceItem
        fields = [
            "id",
            "asset",
            "asset_name",
            "asset_tag",
            "title",
            "description",
            "instructions",
            "estimated_time_minutes",
            "estimated_cost",
            "interval_days",
            "last_completed_at",
            "is_active",
            "is_overdue",
            "days_overdue",
            "next_due_at",
            "materials",
            "tasks",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "is_overdue",
            "days_overdue",
            "next_due_at",
            "created_at",
            "updated_at",
        ]


class MaintenanceLogSerializer(serializers.ModelSerializer):
    """Serializer for maintenance completion records."""

    completed_by_name = serializers.SerializerMethodField()
    maintenance_item_title = serializers.CharField(source="maintenance_item.title", read_only=True)
    asset_name = serializers.CharField(source="maintenance_item.asset.name", read_only=True)

    class Meta:
        model = MaintenanceLog
        fields = [
            "id",
            "maintenance_item",
            "maintenance_item_title",
            "asset_name",
            "completed_by",
            "completed_by_name",
            "completed_at",
            "time_spent_minutes",
            "cost_incurred",
            "notes",
            "created_at",
        ]
        read_only_fields = ["completed_at", "created_at", "completed_by_name"]

    def get_completed_by_name(self, obj):
        if obj.completed_by:
            return obj.completed_by.get_full_name() or obj.completed_by.username
        return None


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


class WorkOrderTaskCompletionSerializer(serializers.ModelSerializer):
    """Serializer for task completion records within a work order."""

    completed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrderTaskCompletion
        fields = [
            "id",
            "work_order",
            "task",
            "task_title",
            "task_order",
            "is_required",
            "is_completed",
            "completed_by",
            "completed_by_name",
            "completed_at",
            "notes",
            "created_at",
        ]
        read_only_fields = ["created_at", "completed_by_name", "task_title", "task_order"]

    def get_completed_by_name(self, obj):
        if obj.completed_by:
            return obj.completed_by.get_full_name() or obj.completed_by.username
        return None


class WorkOrderMaterialUsageSerializer(serializers.ModelSerializer):
    """Serializer for material usage tracking within a work order."""

    class Meta:
        model = WorkOrderMaterialUsage
        fields = [
            "id",
            "work_order",
            "material",
            "material_name",
            "quantity_planned",
            "unit",
            "was_used",
            "created_at",
        ]
        read_only_fields = ["created_at", "material_name", "quantity_planned", "unit"]


class WorkOrderPhotoSerializer(serializers.ModelSerializer):
    """Serializer for photos attached to a work order."""

    uploaded_by_name = serializers.SerializerMethodField()
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrderPhoto
        fields = [
            "id",
            "work_order",
            "image",
            "image_url",
            "caption",
            "uploaded_by",
            "uploaded_by_name",
            "uploaded_at",
        ]
        read_only_fields = ["uploaded_at", "uploaded_by_name", "image_url"]

    def get_uploaded_by_name(self, obj):
        if obj.uploaded_by:
            return obj.uploaded_by.get_full_name() or obj.uploaded_by.username
        return None

    def get_image_url(self, obj):
        request = self.context.get("request")
        if obj.image and request:
            return request.build_absolute_uri(obj.image.url)
        return None


class WorkOrderSubmissionSerializer(serializers.ModelSerializer):
    """Serializer for an inbound (emailed or manually-uploaded) WO submission."""

    pdf_url = serializers.SerializerMethodField()
    submitted_by_name = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrderSubmission
        fields = [
            "id",
            "pdf_url",
            "received_at",
            "status",
            "source",
            "from_email",
            "subject",
            "submitted_by",
            "submitted_by_name",
            "parse_error",
            "pending_changes",
        ]
        read_only_fields = fields

    def get_pdf_url(self, obj):
        if not obj.attachment:
            return None
        request = self.context.get("request")
        url = obj.attachment.url
        return request.build_absolute_uri(url) if request else url

    def get_submitted_by_name(self, obj):
        if obj.submitted_by:
            return obj.submitted_by.get_full_name() or obj.submitted_by.username
        return None


class WorkOrderValidationSerializer(serializers.ModelSerializer):
    """AC-3 audit trail of pre-finalization validation acknowledgements."""

    validated_by_name = serializers.SerializerMethodField()
    is_complete = serializers.ReadOnlyField()

    class Meta:
        model = WorkOrderValidation
        fields = [
            "id",
            "work_order",
            "validated_by",
            "validated_by_name",
            "validated_at",
            "electrical_acknowledged",
            "loto_acknowledged",
            "required_fields_acknowledged",
            "is_complete",
            "notes",
        ]
        read_only_fields = [
            "id",
            "work_order",
            "validated_by",
            "validated_by_name",
            "validated_at",
            "is_complete",
        ]

    def get_validated_by_name(self, obj):
        if obj.validated_by:
            return obj.validated_by.get_full_name() or obj.validated_by.username
        return None


class WorkOrderSerializer(serializers.ModelSerializer):
    """Full serializer for a work order, including nested completions and photos."""

    maintenance_item_title = serializers.CharField(source="maintenance_item.title", read_only=True)
    asset_name = serializers.CharField(source="maintenance_item.asset.name", read_only=True)
    asset_tag = serializers.CharField(source="maintenance_item.asset.asset_tag", read_only=True)
    asset_id = serializers.UUIDField(source="maintenance_item.asset.id", read_only=True)
    assigned_to_name = serializers.SerializerMethodField()
    short_id = serializers.ReadOnlyField()
    is_overdue = serializers.ReadOnlyField()
    task_completions = WorkOrderTaskCompletionSerializer(many=True, read_only=True)
    material_usage = WorkOrderMaterialUsageSerializer(many=True, read_only=True)
    photos = WorkOrderPhotoSerializer(many=True, read_only=True)
    submissions = serializers.SerializerMethodField()
    electrical = serializers.SerializerMethodField()
    loto = serializers.SerializerMethodField()
    validation = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrder
        fields = [
            "id",
            "short_id",
            "maintenance_item",
            "maintenance_item_title",
            "asset_name",
            "asset_tag",
            "asset_id",
            "status",
            "due_date",
            "assigned_to",
            "assigned_to_name",
            "completed_by_name",
            "completed_at",
            "notes",
            "is_overdue",
            "task_completions",
            "material_usage",
            "photos",
            "submissions",
            "electrical",
            "loto",
            "validation",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "short_id",
            "is_overdue",
            "created_at",
            "updated_at",
            "task_completions",
            "material_usage",
            "photos",
            "submissions",
            "electrical",
            "loto",
            "validation",
        ]

    def get_submissions(self, obj):
        qs = obj.submissions.all().order_by("-received_at")
        return WorkOrderSubmissionSerializer(qs, many=True, context=self.context).data

    def get_assigned_to_name(self, obj):
        if obj.assigned_to:
            return obj.assigned_to.get_full_name() or obj.assigned_to.username
        return None

    def get_electrical(self, obj):
        from .services.work_order_context import build_electrical_context

        return build_electrical_context(obj.maintenance_item.asset)

    def get_loto(self, obj):
        from .services.work_order_context import build_loto_context

        return build_loto_context(obj.maintenance_item.asset)

    def get_validation(self, obj):
        latest = obj.validations.order_by("-validated_at").first()
        if latest is None:
            return None
        return WorkOrderValidationSerializer(latest, context=self.context).data


class WorkOrderListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for work order list views."""

    maintenance_item_title = serializers.CharField(source="maintenance_item.title", read_only=True)
    asset_name = serializers.CharField(source="maintenance_item.asset.name", read_only=True)
    asset_tag = serializers.CharField(source="maintenance_item.asset.asset_tag", read_only=True)
    asset_id = serializers.UUIDField(source="maintenance_item.asset.id", read_only=True)
    short_id = serializers.ReadOnlyField()
    is_overdue = serializers.ReadOnlyField()
    task_completion_count = serializers.SerializerMethodField()
    task_total_count = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrder
        fields = [
            "id",
            "short_id",
            "maintenance_item",
            "maintenance_item_title",
            "asset_name",
            "asset_tag",
            "asset_id",
            "status",
            "due_date",
            "is_overdue",
            "completed_by_name",
            "completed_at",
            "task_completion_count",
            "task_total_count",
            "created_at",
            "updated_at",
        ]

    def get_task_completion_count(self, obj):
        return obj.task_completions.filter(is_completed=True).count()

    def get_task_total_count(self, obj):
        return obj.task_completions.count()


class StockReconciliationSerializer(serializers.ModelSerializer):
    """Read serializer for StockReconciliation audit rows."""

    item_name = serializers.CharField(source="item.name", read_only=True)
    item_sku = serializers.CharField(source="item.sku", read_only=True)
    reconciled_by_name = serializers.SerializerMethodField()
    triggered_reorder_id = serializers.PrimaryKeyRelatedField(
        source="triggered_reorder", read_only=True
    )

    class Meta:
        model = StockReconciliation
        fields = [
            "id",
            "item",
            "item_name",
            "item_sku",
            "projected_count",
            "actual_count",
            "delta",
            "reason",
            "notes",
            "reconciled_by",
            "reconciled_by_name",
            "reconciled_at",
            "triggered_reorder",
            "triggered_reorder_id",
        ]
        read_only_fields = fields

    def get_reconciled_by_name(self, obj):
        user = obj.reconciled_by
        if not user:
            return ""
        full = (getattr(user, "get_full_name", lambda: "")() or "").strip()
        return full or getattr(user, "username", "") or getattr(user, "email", "")


class StockReconciliationRowSerializer(serializers.Serializer):
    """A single row in a batch reconciliation submission."""

    item_id = serializers.UUIDField()
    actual_count = serializers.IntegerField(min_value=0)
    reason = serializers.ChoiceField(choices=StockReconciliation.REASON_CHOICES)
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    skip_reorder = serializers.BooleanField(required=False, default=False)


class StockReconciliationBatchSerializer(serializers.Serializer):
    """Batch payload: a list of reconciliation rows."""

    rows = StockReconciliationRowSerializer(many=True, allow_empty=False)


class AssetTcoReportSerializer(serializers.Serializer):
    """One row in the per-asset Total Cost of Ownership report."""

    asset_id = serializers.UUIDField()
    asset_name = serializers.CharField()
    asset_tag = serializers.CharField(allow_blank=True)
    maintenance_days_last_90 = serializers.IntegerField()
    scheduled_maintenance_cost = serializers.DecimalField(max_digits=12, decimal_places=2)
    unscheduled_maintenance_cost = serializers.DecimalField(max_digits=12, decimal_places=2)
    repair_cost = serializers.DecimalField(max_digits=12, decimal_places=2)
    tco = serializers.DecimalField(max_digits=12, decimal_places=2)
    preventive_maintenance_cost = serializers.DecimalField(max_digits=12, decimal_places=2)
    vendor_maintenance_cost = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_maintenance_cost_90d = serializers.DecimalField(max_digits=12, decimal_places=2)


class LocationReconcileItemSerializer(serializers.ModelSerializer):
    """Single item row in the location reconcile grid payload."""

    item_id = serializers.UUIDField(source="id", read_only=True)
    projected = serializers.IntegerField(source="current_stock", read_only=True)
    owning_group_name = serializers.CharField(
        source="owning_group.name", read_only=True, default=""
    )

    class Meta:
        model = InventoryItem
        fields = [
            "item_id",
            "name",
            "sku",
            "projected",
            "minimum_stock",
            "reorder_quantity",
            "owning_group_name",
        ]
        read_only_fields = fields


class MaintenanceRecordSerializer(serializers.ModelSerializer):
    """Serializer for backdated/recent maintenance records on an asset."""

    asset_name = serializers.CharField(source="asset.name", read_only=True)
    vendor_name = serializers.CharField(source="vendor.name", read_only=True, default=None)
    performed_by_internal_username = serializers.CharField(
        source="performed_by_internal.username", read_only=True, default=None
    )
    recorded_by_username = serializers.CharField(
        source="recorded_by.username", read_only=True, default=None
    )
    attachment_url = serializers.SerializerMethodField()

    class Meta:
        model = MaintenanceRecord
        fields = [
            "id",
            "asset",
            "asset_name",
            "title",
            "description",
            "completed_on",
            "vendor",
            "vendor_name",
            "performed_by_internal",
            "performed_by_internal_username",
            "cost",
            "invoice_number",
            "attachment",
            "attachment_url",
            "notes",
            "recorded_by",
            "recorded_by_username",
            "recorded_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "recorded_by",
            "recorded_at",
            "updated_at",
        ]

    def get_attachment_url(self, obj):
        if not obj.attachment:
            return None
        request = self.context.get("request")
        url = obj.attachment.url
        if request is not None:
            return request.build_absolute_uri(url)
        return url

    def validate(self, attrs):
        attrs = super().validate(attrs)
        vendor = attrs.get("vendor", getattr(self.instance, "vendor", None))
        internal = attrs.get(
            "performed_by_internal", getattr(self.instance, "performed_by_internal", None)
        )
        if vendor is None and internal is None:
            raise serializers.ValidationError(
                {
                    "performed_by_internal": (
                        "Either a vendor or an internal staff member must be set."
                    )
                }
            )
        completed_on = attrs.get("completed_on", getattr(self.instance, "completed_on", None))
        from django.utils import timezone as _tz

        if completed_on is not None and completed_on > _tz.localdate():
            raise serializers.ValidationError(
                {"completed_on": "completed_on cannot be in the future."}
            )
        return attrs


class AssetReservationSerializer(serializers.ModelSerializer):
    """Per-asset reservation for a class / training / event.

    `reserved_by` is read-only — the viewset injects request.user on
    create so the caller can't pin a reservation to someone else.
    `is_current` is computed server-side and surfaces "the e-paper
    panel will be showing this RIGHT NOW" to clients.
    """

    asset_name = serializers.CharField(source="asset.name", read_only=True)
    reserved_by_username = serializers.CharField(source="reserved_by.username", read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    is_current = serializers.BooleanField(read_only=True)

    class Meta:
        model = AssetReservation
        fields = [
            "id",
            "asset",
            "asset_name",
            "title",
            "reserved_by",
            "reserved_by_username",
            "starts_at",
            "ends_at",
            "notes",
            "cancelled_at",
            "is_active",
            "is_current",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "reserved_by",
            "reserved_by_username",
            "asset_name",
            "is_active",
            "is_current",
            "cancelled_at",
            "created_at",
            "updated_at",
        ]


class AssetOutOfServiceSerializer(serializers.ModelSerializer):
    """OOS event against an asset. POST opens, /restore/ closes."""

    asset_name = serializers.CharField(source="asset.name", read_only=True)
    placed_by_username = serializers.CharField(source="placed_by.username", read_only=True)
    restored_by_username = serializers.CharField(
        source="restored_by.username", read_only=True, allow_null=True
    )
    is_open = serializers.BooleanField(read_only=True)

    class Meta:
        model = AssetOutOfService
        fields = [
            "id",
            "asset",
            "asset_name",
            "placed_out_at",
            "placed_by",
            "placed_by_username",
            "expected_return_at",
            "reason",
            "restored_at",
            "restored_by",
            "restored_by_username",
            "is_open",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "asset_name",
            "placed_out_at",
            "placed_by",
            "placed_by_username",
            "restored_at",
            "restored_by",
            "restored_by_username",
            "is_open",
            "created_at",
            "updated_at",
        ]
