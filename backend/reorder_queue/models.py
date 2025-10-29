"""
Models for reorder queue management.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from inventory.models import InventoryItem, ItemSupplier, Supplier


class ReorderRequest(models.Model):
    """
    Tracks requests to reorder items.

    Created when users scan QR codes and request reorders.
    Manages the full lifecycle from request to delivery:
    - Pending: Initial request submitted
    - Approved: Admin has approved the request
    - Ordered: Order placed with supplier
    - Received: Items delivered and stocked
    - Cancelled: Request cancelled
    """

    # Status choices
    PENDING = "pending"
    APPROVED = "approved"
    ORDERED = "ordered"
    RECEIVED = "received"
    CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (APPROVED, "Approved"),
        (ORDERED, "Ordered"),
        (RECEIVED, "Received"),
        (CANCELLED, "Cancelled"),
    ]

    # Priority choices
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"

    PRIORITY_CHOICES = [
        (LOW, "Low"),
        (NORMAL, "Normal"),
        (HIGH, "High"),
        (URGENT, "Urgent"),
    ]

    item = models.ForeignKey(
        InventoryItem, on_delete=models.CASCADE, related_name="reorder_requests"
    )
    quantity = models.PositiveIntegerField(help_text="Quantity requested to reorder")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default="normal")

    # Request information
    requested_by = models.CharField(
        max_length=100, blank=True, help_text="Name or ID of person requesting reorder"
    )
    request_notes = models.TextField(blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)

    # Admin handling
    reviewed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="reviewed_reorders"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    admin_notes = models.TextField(blank=True)

    # Order tracking
    ordered_at = models.DateTimeField(null=True, blank=True)
    estimated_delivery = models.DateField(null=True, blank=True)
    actual_delivery = models.DateField(null=True, blank=True)
    order_number = models.CharField(max_length=100, blank=True)
    actual_cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # Metadata
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-requested_at"]
        indexes = [
            models.Index(fields=["status", "-requested_at"]),
            models.Index(fields=["item", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.item.name} - {self.quantity} units ({self.status})"

    @property
    def estimated_cost(self) -> Optional[Decimal]:
        """Calculate estimated cost based on item unit cost."""
        if self.item.unit_cost:
            return self.quantity * self.item.unit_cost
        return None

    @property
    def days_pending(self) -> int:
        """Calculate how many days the request has been pending."""
        if self.status == self.PENDING:
            return (timezone.now() - self.requested_at).days
        return 0


class PurchaseOrder(models.Model):
    """
    Purchase order placed with a supplier.

    Groups multiple items from the same supplier into a single order.
    Tracks the complete lifecycle from creation to delivery.
    """

    # Status choices
    DRAFT = "draft"
    SENT = "sent"
    CONFIRMED = "confirmed"
    PARTIALLY_RECEIVED = "partially_received"
    RECEIVED = "received"
    CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (DRAFT, "Draft"),
        (SENT, "Sent to Supplier"),
        (CONFIRMED, "Confirmed by Supplier"),
        (PARTIALLY_RECEIVED, "Partially Received"),
        (RECEIVED, "Fully Received"),
        (CANCELLED, "Cancelled"),
    ]

    # Core fields
    po_number = models.CharField(max_length=50, unique=True, help_text="Purchase Order Number")
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name="purchase_orders")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=DRAFT)

    # Order details
    order_date = models.DateTimeField(auto_now_add=True)
    expected_delivery_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    # Financial tracking
    estimated_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Estimated total cost of the order",
    )
    actual_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Actual total cost after delivery",
    )

    # User tracking
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="created_orders")
    sent_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="sent_orders"
    )
    sent_at = models.DateTimeField(null=True, blank=True)

    # Metadata
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-order_date"]
        indexes = [
            models.Index(fields=["supplier", "status"]),
            models.Index(fields=["status", "-order_date"]),
            models.Index(fields=["po_number"]),
        ]

    def __str__(self) -> str:
        return f"PO #{self.po_number} - {self.supplier.name} ({self.status})"

    @property
    def total_items(self) -> int:
        """Total number of distinct items in this order."""
        return self.items.count()

    @property
    def total_quantity(self) -> int:
        """Total quantity of all items ordered."""
        return sum(item.quantity_ordered for item in self.items.all())

    @property
    def total_received_quantity(self) -> int:
        """Total quantity of all items received."""
        return sum(item.quantity_received for item in self.items.all())

    @property
    def is_fully_received(self) -> bool:
        """Check if all ordered items have been fully received."""
        return all(item.is_fully_received for item in self.items.all())

    @property
    def days_since_ordered(self) -> int:
        """Days since the order was sent to supplier."""
        if self.sent_at:
            return (timezone.now() - self.sent_at).days
        return 0

    def calculate_estimated_total(self) -> Decimal:
        """Calculate estimated total cost from all line items."""
        total = sum(item.estimated_cost for item in self.items.all())
        self.estimated_total = total
        return total

    def auto_generate_po_number(self) -> str:
        """Auto-generate a PO number if not set."""
        if not self.po_number:
            # Format: PO-YYYY-NNNN
            year = timezone.now().year
            last_po = (
                PurchaseOrder.objects.filter(po_number__startswith=f"PO-{year}-")
                .order_by("-po_number")
                .first()
            )

            if last_po:
                try:
                    last_num = int(last_po.po_number.split("-")[-1])
                    next_num = last_num + 1
                except (ValueError, IndexError):
                    next_num = 1
            else:
                next_num = 1

            self.po_number = f"PO-{year}-{next_num:04d}"
        return self.po_number


class PurchaseOrderItem(models.Model):
    """
    Line item within a purchase order.

    Represents a specific item and quantity ordered from a supplier.
    Tracks received quantities and costs.
    """

    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name="items"
    )
    item_supplier = models.ForeignKey(
        ItemSupplier,
        on_delete=models.CASCADE,
        help_text="Specific supplier relationship for this item",
    )

    # Order quantities
    quantity_ordered = models.PositiveIntegerField(
        validators=[MinValueValidator(1)], help_text="Quantity ordered from supplier"
    )
    quantity_received = models.PositiveIntegerField(
        default=0, help_text="Quantity actually received"
    )

    # Pricing
    unit_cost_ordered = models.DecimalField(
        max_digits=10, decimal_places=4, help_text="Unit cost at time of ordering"
    )
    unit_cost_actual = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True, help_text="Actual unit cost charged"
    )

    # Notes
    notes = models.TextField(blank=True)

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["purchase_order", "item_supplier__item__name"]
        unique_together = ["purchase_order", "item_supplier"]
        indexes = [
            models.Index(fields=["purchase_order", "item_supplier"]),
        ]

    def __str__(self) -> str:
        return f"{self.item_supplier.item.name} - {self.quantity_ordered} units"

    @property
    def item(self) -> InventoryItem:
        """Convenience property to access the inventory item."""
        return self.item_supplier.item

    @property
    def supplier(self) -> Supplier:
        """Convenience property to access the supplier."""
        return self.item_supplier.supplier

    @property
    def estimated_cost(self) -> Decimal:
        """Calculate estimated total cost for this line item."""
        return self.quantity_ordered * self.unit_cost_ordered

    @property
    def actual_cost(self) -> Optional[Decimal]:
        """Calculate actual total cost if unit cost is known."""
        if self.unit_cost_actual is not None and self.quantity_received > 0:
            return self.quantity_received * self.unit_cost_actual
        return None

    @property
    def is_fully_received(self) -> bool:
        """Check if the full ordered quantity has been received."""
        return self.quantity_received >= self.quantity_ordered

    @property
    def quantity_pending(self) -> int:
        """Calculate quantity still pending delivery."""
        return max(0, self.quantity_ordered - self.quantity_received)


class OrderDelivery(models.Model):
    """
    Tracks a delivery/receipt event for a purchase order.

    A purchase order may have multiple deliveries (partial shipments).
    Each delivery records what was actually received and when.
    """

    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name="deliveries"
    )

    # Delivery details
    delivery_date = models.DateTimeField(default=timezone.now)
    tracking_number = models.CharField(max_length=100, blank=True)
    carrier = models.CharField(max_length=100, blank=True)

    # Receipt details
    received_by = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="received_deliveries"
    )
    receipt_notes = models.TextField(blank=True)

    # Status
    is_complete = models.BooleanField(
        default=False, help_text="Mark as complete when all items in this delivery are processed"
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-delivery_date"]
        indexes = [
            models.Index(fields=["purchase_order", "-delivery_date"]),
            models.Index(fields=["received_by", "-delivery_date"]),
        ]

    def __str__(self) -> str:
        return f"Delivery for PO #{self.purchase_order.po_number} on {self.delivery_date.date()}"

    @property
    def total_items_received(self) -> int:
        """Total number of distinct items received in this delivery."""
        return self.items.count()

    @property
    def total_quantity_received(self) -> int:
        """Total quantity of all items received in this delivery."""
        return sum(item.quantity_received for item in self.items.all())


class DeliveryItem(models.Model):
    """
    Individual item received in a delivery.

    Records the actual quantity and condition of each item received.
    Used for barcode scanning and inventory updates.
    """

    delivery = models.ForeignKey(OrderDelivery, on_delete=models.CASCADE, related_name="items")
    purchase_order_item = models.ForeignKey(
        PurchaseOrderItem, on_delete=models.CASCADE, related_name="deliveries"
    )

    # Receipt details
    quantity_received = models.PositiveIntegerField(
        validators=[MinValueValidator(1)], help_text="Quantity actually received for this item"
    )

    # Quality/condition tracking
    is_damaged = models.BooleanField(default=False)
    is_expired = models.BooleanField(default=False)
    condition_notes = models.TextField(blank=True)

    # Barcode/scanning info
    scanned_upc = models.CharField(
        max_length=50, blank=True, help_text="UPC code scanned during receipt"
    )
    scanned_at = models.DateTimeField(null=True, blank=True)
    scanned_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="scanned_items"
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["delivery", "purchase_order_item"]
        indexes = [
            models.Index(fields=["delivery", "purchase_order_item"]),
            models.Index(fields=["scanned_upc", "scanned_at"]),
        ]

    def __str__(self) -> str:
        item_name = self.purchase_order_item.item.name
        return f"{item_name} - {self.quantity_received} received"

    @property
    def item(self) -> InventoryItem:
        """Convenience property to access the inventory item."""
        return self.purchase_order_item.item

    @property
    def supplier(self) -> Supplier:
        """Convenience property to access the supplier."""
        return self.purchase_order_item.supplier


class LeadTimeLog(models.Model):
    """
    Historical lead time tracking for supplier performance analysis.

    Records actual delivery performance vs. estimated lead times
    to improve future ordering decisions and supplier evaluation.
    """

    item_supplier = models.ForeignKey(
        ItemSupplier, on_delete=models.CASCADE, related_name="lead_time_logs"
    )
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name="lead_time_logs"
    )

    # Time tracking
    order_date = models.DateTimeField(help_text="When the order was placed")
    expected_delivery_date = models.DateField(help_text="When delivery was expected")
    actual_delivery_date = models.DateField(help_text="When delivery actually occurred")

    # Lead time calculations (in business days)
    estimated_lead_time_days = models.PositiveIntegerField(
        help_text="Estimated lead time in business days"
    )
    actual_lead_time_days = models.PositiveIntegerField(
        help_text="Actual lead time in business days"
    )
    variance_days = models.IntegerField(
        help_text="Difference between actual and estimated (positive = late)"
    )

    # Order details
    quantity_ordered = models.PositiveIntegerField()
    quantity_received = models.PositiveIntegerField()

    # Metadata
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-actual_delivery_date"]
        indexes = [
            models.Index(fields=["item_supplier", "-actual_delivery_date"]),
            models.Index(fields=["purchase_order"]),
            models.Index(fields=["actual_delivery_date"]),
        ]

    def __str__(self) -> str:
        item_name = self.item_supplier.item.name
        supplier_name = self.item_supplier.supplier.name
        return f"{item_name} from {supplier_name} - {self.actual_lead_time_days} days"

    @property
    def item(self) -> InventoryItem:
        """Convenience property to access the inventory item."""
        return self.item_supplier.item

    @property
    def supplier(self) -> Supplier:
        """Convenience property to access the supplier."""
        return self.item_supplier.supplier

    @property
    def was_late(self) -> bool:
        """Check if the delivery was late."""
        return self.variance_days > 0

    @property
    def was_early(self) -> bool:
        """Check if the delivery was early."""
        return self.variance_days < 0

    @classmethod
    def calculate_business_days(cls, start_date, end_date) -> int:
        """Calculate business days between two dates (excluding weekends)."""
        from datetime import timedelta

        if isinstance(start_date, timezone.datetime):
            start_date = start_date.date()
        if isinstance(end_date, timezone.datetime):
            end_date = end_date.date()

        if start_date > end_date:
            return 0

        business_days = 0
        current_date = start_date

        while current_date <= end_date:
            # Monday = 0, Sunday = 6
            if current_date.weekday() < 5:  # Monday to Friday
                business_days += 1
            current_date += timedelta(days=1)

        return business_days

    def save(self, *args, **kwargs):
        """Auto-calculate variance when saving."""
        self.variance_days = self.actual_lead_time_days - self.estimated_lead_time_days
        super().save(*args, **kwargs)
