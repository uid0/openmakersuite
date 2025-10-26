"""
Serializers for inventory API.
"""

from rest_framework import serializers

from .models import Category, InventoryItem, Supplier, UsageLog


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


class InventoryItemSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)
    needs_reorder = serializers.BooleanField(read_only=True)
    total_value = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    image = serializers.ImageField(read_only=True)
    thumbnail = serializers.SerializerMethodField()
    supplier = serializers.SerializerMethodField()

    class Meta:
        model = InventoryItem
        fields = [
            "id",
            "name",
            "description",
            "sku",
            "image",
            "thumbnail",
            "category",
            "category_name",
            "location",
            "reorder_quantity",
            "current_stock",
            "minimum_stock",
            "supplier",
            "supplier_name",
            "supplier_sku",
            "supplier_url",
            "unit_cost",
            "package_cost",
            "quantity_per_package",
            "average_lead_time",
            "qr_code",
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

    def get_supplier(self, obj):
        """Return the primary supplier identifier for backward compatibility."""

        supplier = obj.supplier
        return str(supplier.pk) if supplier else None


class InventoryItemDetailSerializer(InventoryItemSerializer):
    """Extended serializer with related data."""

    recent_usage = UsageLogSerializer(source="usage_logs", many=True, read_only=True)
    supplier_details = SupplierSerializer(source="supplier", read_only=True)
    category_details = CategorySerializer(source="category", read_only=True)

    class Meta(InventoryItemSerializer.Meta):
        fields = InventoryItemSerializer.Meta.fields + [
            "recent_usage",
            "supplier_details",
            "category_details",
        ]
