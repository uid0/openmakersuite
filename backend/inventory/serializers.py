"""
Serializers for inventory API.
"""

from rest_framework import serializers

from .models import (Category, InventoryItem, ItemSupplier, PriceHistory,
                     Supplier, UsageLog)


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
    recent_price_history = PriceHistorySerializer(source="price_history", many=True, read_only=True)

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
            "notes",
            "created_at",
            "updated_at",
            "recent_price_history",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def to_representation(self, instance):
        """Limit price history to recent records for performance."""
        data = super().to_representation(instance)
        # Limit to most recent 10 price history records
        if "recent_price_history" in data:
            data["recent_price_history"] = data["recent_price_history"][:10]
        return data


class InventoryItemSerializer(serializers.ModelSerializer):
    # Primary supplier fields (for backward compatibility)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)
    needs_reorder = serializers.BooleanField(read_only=True)
    total_value = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    image = serializers.ImageField(read_only=True)
    thumbnail = serializers.SerializerMethodField()
    qr_code_url = serializers.SerializerMethodField()
    location = serializers.CharField(source="location.name", read_only=True)

    # Complete supplier information array
    suppliers = ItemSupplierSerializer(source="item_suppliers", many=True, read_only=True)

    # Hazmat calculated fields
    nfpa_fire_diamond_display = serializers.ReadOnlyField()
    hazmat_compliance_status = serializers.ReadOnlyField()
    has_complete_nfpa_data = serializers.ReadOnlyField()

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
            # Hazmat fields
            "is_hazardous",
            "msds_url",
            "nfpa_health_hazard",
            "nfpa_fire_hazard",
            "nfpa_instability_hazard",
            "nfpa_special_hazards",
            "nfpa_fire_diamond_display",
            "hazmat_compliance_status",
            "has_complete_nfpa_data",
            "is_active",
            "notes",
            "needs_reorder",
            "total_value",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["qr_code", "created_at", "updated_at"]

    def get_thumbnail(self, obj):
        """Return the thumbnail URL when available."""

        try:
            return obj.thumbnail.url if obj.thumbnail else None
        except Exception:
            return None

    def get_qr_code_url(self, obj):
        """Return the QR code URL when available."""

        try:
            return obj.qr_code.url if obj.qr_code else None
        except Exception:
            return None


class InventoryItemDetailSerializer(InventoryItemSerializer):
    """Extended serializer with related data."""

    recent_usage = UsageLogSerializer(source="usage_logs", many=True, read_only=True)
    supplier_details = SupplierSerializer(source="supplier", read_only=True)
    category_details = CategorySerializer(source="category", read_only=True)
    all_suppliers = ItemSupplierSerializer(source="item_suppliers", many=True, read_only=True)
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
