"""
Serializers for reorder queue API.
"""

from rest_framework import serializers

from inventory.models import ItemSupplier
from inventory.serializers import InventoryItemSerializer, ItemSupplierSerializer

from .models import (
    DeliveryItem,
    LeadTimeLog,
    OrderDelivery,
    PurchaseOrder,
    PurchaseOrderItem,
    ReorderRequest,
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
            # Transparency fields
            "invoice_number",
            "invoice_url",
            "purchase_order_url",
            "delivery_tracking_url",
            "supplier_url",
            "public_notes",
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


# Purchase Order Serializers


class PurchaseOrderItemSerializer(serializers.ModelSerializer):
    """Serializer for purchase order line items."""

    # Related data
    item_details = InventoryItemSerializer(source="item", read_only=True)
    supplier_details = serializers.CharField(source="supplier.name", read_only=True)
    item_supplier_details = ItemSupplierSerializer(source="item_supplier", read_only=True)

    # Calculated fields
    estimated_cost = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    actual_cost = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    quantity_pending = serializers.IntegerField(read_only=True)
    is_fully_received = serializers.BooleanField(read_only=True)

    class Meta:
        model = PurchaseOrderItem
        fields = [
            "id",
            "purchase_order",
            "item_supplier",
            "item_supplier_details",
            "item_details",
            "supplier_details",
            "quantity_ordered",
            "quantity_received",
            "unit_cost_ordered",
            "unit_cost_actual",
            "estimated_cost",
            "actual_cost",
            "quantity_pending",
            "is_fully_received",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]


class PurchaseOrderSerializer(serializers.ModelSerializer):
    """Comprehensive serializer for purchase orders."""

    # Related data
    supplier_details = serializers.CharField(source="supplier.name", read_only=True)
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)
    sent_by_username = serializers.CharField(
        source="sent_by.username", read_only=True, allow_null=True
    )

    # Line items
    items = PurchaseOrderItemSerializer(many=True, read_only=True)

    # Calculated fields
    total_items = serializers.IntegerField(read_only=True)
    total_quantity = serializers.IntegerField(read_only=True)
    total_received_quantity = serializers.IntegerField(read_only=True)
    is_fully_received = serializers.BooleanField(read_only=True)
    days_since_ordered = serializers.IntegerField(read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = [
            "id",
            "po_number",
            "supplier",
            "supplier_details",
            "status",
            "order_date",
            "expected_delivery_date",
            "notes",
            "estimated_total",
            "actual_total",
            "created_by",
            "created_by_username",
            "sent_by",
            "sent_by_username",
            "sent_at",
            "updated_at",
            # Related data
            "items",
            # Calculated fields
            "total_items",
            "total_quantity",
            "total_received_quantity",
            "is_fully_received",
            "days_since_ordered",
        ]
        read_only_fields = ["po_number", "order_date", "updated_at"]


class PurchaseOrderCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating purchase orders with line items."""

    items = serializers.ListField(
        child=serializers.DictField(),
        write_only=True,
        help_text="List of items to order with quantities",
    )

    class Meta:
        model = PurchaseOrder
        fields = [
            "supplier",
            "expected_delivery_date",
            "notes",
            "items",  # Format: [{"item_supplier_id": 1, "quantity": 10}, ...]
        ]

    def create(self, validated_data):
        """Create purchase order with line items."""
        items_data = validated_data.pop("items")

        # Create the purchase order
        purchase_order = PurchaseOrder.objects.create(
            created_by=self.context["request"].user, **validated_data
        )

        # Generate PO number
        purchase_order.auto_generate_po_number()
        purchase_order.save()

        # Create line items
        total_cost = 0
        for item_data in items_data:
            item_supplier_id = item_data["item_supplier_id"]
            quantity = item_data["quantity"]

            try:
                item_supplier = ItemSupplier.objects.get(id=item_supplier_id)

                # Ensure the supplier matches the PO supplier
                if item_supplier.supplier != purchase_order.supplier:
                    raise serializers.ValidationError(
                        f"Item supplier {item_supplier_id} does not belong to selected supplier"
                    )

                # Create the line item
                line_item = PurchaseOrderItem.objects.create(
                    purchase_order=purchase_order,
                    item_supplier=item_supplier,
                    quantity_ordered=quantity,
                    unit_cost_ordered=item_supplier.unit_cost or 0,
                    notes=item_data.get("notes", ""),
                )

                total_cost += line_item.estimated_cost

            except ItemSupplier.DoesNotExist:
                raise serializers.ValidationError(
                    f"ItemSupplier with id {item_supplier_id} does not exist"
                )

        # Update estimated total
        purchase_order.estimated_total = total_cost
        purchase_order.save()

        return purchase_order


# Delivery and Receipt Serializers


class DeliveryItemSerializer(serializers.ModelSerializer):
    """Serializer for individual items received in a delivery."""

    # Related data
    item_details = InventoryItemSerializer(source="item", read_only=True)
    supplier_details = serializers.CharField(source="supplier.name", read_only=True)
    scanned_by_username = serializers.CharField(
        source="scanned_by.username", read_only=True, allow_null=True
    )

    class Meta:
        model = DeliveryItem
        fields = [
            "id",
            "delivery",
            "purchase_order_item",
            "item_details",
            "supplier_details",
            "quantity_received",
            "is_damaged",
            "is_expired",
            "condition_notes",
            "scanned_upc",
            "scanned_at",
            "scanned_by",
            "scanned_by_username",
            "created_at",
        ]
        read_only_fields = ["created_at"]


class OrderDeliverySerializer(serializers.ModelSerializer):
    """Serializer for order deliveries/receipts."""

    # Related data
    purchase_order_details = PurchaseOrderSerializer(source="purchase_order", read_only=True)
    received_by_username = serializers.CharField(source="received_by.username", read_only=True)

    # Items received in this delivery
    items = DeliveryItemSerializer(many=True, read_only=True)

    # Calculated fields
    total_items_received = serializers.IntegerField(read_only=True)
    total_quantity_received = serializers.IntegerField(read_only=True)

    class Meta:
        model = OrderDelivery
        fields = [
            "id",
            "purchase_order",
            "purchase_order_details",
            "delivery_date",
            "tracking_number",
            "carrier",
            "received_by",
            "received_by_username",
            "receipt_notes",
            "is_complete",
            "items",
            "total_items_received",
            "total_quantity_received",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]


class BarcodeReceiptSerializer(serializers.Serializer):
    """Serializer for barcode scanning during order receipt."""

    purchase_order_id = serializers.IntegerField()
    scanned_upc = serializers.CharField(max_length=50)
    quantity_received = serializers.IntegerField(min_value=1)
    is_damaged = serializers.BooleanField(default=False)
    is_expired = serializers.BooleanField(default=False)
    condition_notes = serializers.CharField(max_length=500, required=False, allow_blank=True)

    def validate_purchase_order_id(self, value):
        """Validate that the purchase order exists and is in the correct state."""
        try:
            po = PurchaseOrder.objects.get(id=value)
            if po.status not in [
                PurchaseOrder.SENT,
                PurchaseOrder.CONFIRMED,
                PurchaseOrder.PARTIALLY_RECEIVED,
            ]:
                raise serializers.ValidationError(
                    "Purchase order must be sent, confirmed, or partially received to accept deliveries"
                )
            return value
        except PurchaseOrder.DoesNotExist:
            raise serializers.ValidationError("Purchase order does not exist")


# Lead Time and Analytics Serializers


class LeadTimeLogSerializer(serializers.ModelSerializer):
    """Serializer for lead time tracking data."""

    # Related data
    item_details = InventoryItemSerializer(source="item", read_only=True)
    supplier_details = serializers.CharField(source="supplier.name", read_only=True)
    purchase_order_details = serializers.CharField(
        source="purchase_order.po_number", read_only=True
    )

    # Calculated fields
    was_late = serializers.BooleanField(read_only=True)
    was_early = serializers.BooleanField(read_only=True)

    class Meta:
        model = LeadTimeLog
        fields = [
            "id",
            "item_supplier",
            "item_details",
            "supplier_details",
            "purchase_order",
            "purchase_order_details",
            "order_date",
            "expected_delivery_date",
            "actual_delivery_date",
            "estimated_lead_time_days",
            "actual_lead_time_days",
            "variance_days",
            "quantity_ordered",
            "quantity_received",
            "was_late",
            "was_early",
            "recorded_at",
        ]
        read_only_fields = ["recorded_at"]


# Dashboard and Analytics Serializers


class OrderMetricsSerializer(serializers.Serializer):
    """Serializer for order dashboard metrics."""

    # Order counts
    total_orders = serializers.IntegerField()
    draft_orders = serializers.IntegerField()
    sent_orders = serializers.IntegerField()
    confirmed_orders = serializers.IntegerField()
    partially_received_orders = serializers.IntegerField()
    completed_orders = serializers.IntegerField()

    # Item metrics
    total_items_on_order = serializers.IntegerField()
    total_items_received = serializers.IntegerField()
    items_pending_receipt = serializers.IntegerField()

    # Financial metrics
    total_order_value = serializers.DecimalField(max_digits=15, decimal_places=2)
    received_order_value = serializers.DecimalField(max_digits=15, decimal_places=2)
    pending_order_value = serializers.DecimalField(max_digits=15, decimal_places=2)

    # Recent activity
    orders_created_this_week = serializers.IntegerField()
    orders_received_this_week = serializers.IntegerField()

    # Lead time metrics
    average_lead_time_days = serializers.FloatField()
    on_time_delivery_rate = serializers.FloatField()  # Percentage


class SupplierPerformanceSerializer(serializers.Serializer):
    """Serializer for supplier performance metrics."""

    supplier_id = serializers.IntegerField()
    supplier_name = serializers.CharField()

    # Order metrics
    total_orders = serializers.IntegerField()
    completed_orders = serializers.IntegerField()
    active_orders = serializers.IntegerField()

    # Delivery performance
    average_lead_time_days = serializers.FloatField()
    on_time_delivery_rate = serializers.FloatField()
    early_delivery_rate = serializers.FloatField()
    late_delivery_rate = serializers.FloatField()

    # Financial metrics
    total_order_value = serializers.DecimalField(max_digits=15, decimal_places=2)

    # Quality metrics
    damage_rate = serializers.FloatField()  # Percentage of damaged items

    # Recent activity
    last_order_date = serializers.DateTimeField(allow_null=True)
    days_since_last_order = serializers.IntegerField(allow_null=True)
