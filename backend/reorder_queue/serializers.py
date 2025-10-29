"""
Serializers for reorder queue API.
"""

from rest_framework import serializers

from inventory.serializers import InventoryItemSerializer, ItemSupplierSerializer
from inventory.models import ItemSupplier

from .models import (
    ReorderRequest,
    PurchaseOrder,
    PurchaseOrderItem,
    OrderDelivery,
    DeliveryItem,
    LeadTimeLog,
)


class ReorderRequestSerializer(serializers.ModelSerializer):
    item_details = InventoryItemSerializer(source="item", read_only=True)
    estimated_cost = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    days_pending = serializers.IntegerField(read_only=True)
    reviewed_by_username = serializers.CharField(
        source="reviewed_by.username", read_only=True, allow_null=True
    )

    class Meta:
        model = ReorderRequest
        fields = [
            "id",
            "item",
            "item_details",
            "quantity",
            "status",
            "priority",
            "requested_by",
            "request_notes",
            "requested_at",
            "reviewed_by",
            "reviewed_by_username",
            "reviewed_at",
            "admin_notes",
            "ordered_at",
            "estimated_delivery",
            "actual_delivery",
            "order_number",
            "actual_cost",
            "estimated_cost",
            "days_pending",
            "updated_at",
        ]
        read_only_fields = ["requested_at", "updated_at"]


class ReorderRequestCreateSerializer(serializers.ModelSerializer):
    """Simplified serializer for creating reorder requests (public-facing)."""

    class Meta:
        model = ReorderRequest
        fields = ["item", "quantity", "requested_by", "request_notes", "priority"]
        extra_kwargs = {
            "requested_by": {"required": False},
            "request_notes": {"required": False},
            "priority": {"required": False},
        }
