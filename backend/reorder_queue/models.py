"""
Models for reorder queue management.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.validators import MinValueValidator
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from inventory.models import InventoryItem, ItemSupplier, Supplier

User = get_user_model()


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
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_reorders",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    admin_notes = models.TextField(blank=True)

    # Order tracking
    ordered_at = models.DateTimeField(null=True, blank=True)
    estimated_delivery = models.DateField(null=True, blank=True)
    actual_delivery = models.DateField(null=True, blank=True)
    order_number = models.CharField(max_length=100, blank=True)
    actual_cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # Transparency tracking (public information)
    invoice_number = models.CharField(
        max_length=100, blank=True, help_text="Invoice/receipt number for transparency"
    )
    invoice_url = models.URLField(
        blank=True, help_text="Link to invoice/receipt (if publicly available)"
    )
    purchase_order_url = models.URLField(blank=True, help_text="Link to purchase order document")
    delivery_tracking_url = models.URLField(
        blank=True, help_text="Link to shipping/delivery tracking"
    )
    supplier_url = models.URLField(blank=True, help_text="Link to supplier item page")
    public_notes = models.TextField(
        blank=True, help_text="Public notes visible in transparency view"
    )

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
        if self.status == self.PENDING and self.requested_at:
            return (timezone.now() - self.requested_at).days
        return 0

    @property
    def has_transparency_data(self) -> bool:
        """Check if this request has financial transparency information to display."""
        return bool(
            self.actual_cost
            or self.invoice_number
            or self.invoice_url
            or self.purchase_order_url
            or self.delivery_tracking_url
            or self.order_number
        )

    @property
    def cost_per_unit(self) -> Optional[Decimal]:
        """Calculate cost per unit if actual cost is available."""
        if self.actual_cost and self.quantity > 0:
            return self.actual_cost / self.quantity
        return None


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
    VOIDED = "voided"

    STATUS_CHOICES = [
        (DRAFT, "Draft"),
        (SENT, "Sent to Supplier"),
        (CONFIRMED, "Confirmed by Supplier"),
        (PARTIALLY_RECEIVED, "Partially Received"),
        (RECEIVED, "Fully Received"),
        (CANCELLED, "Cancelled"),
        (VOIDED, "Voided"),
    ]

    # Core fields
    po_number = models.CharField(
        max_length=50,
        unique=True,
        blank=True,
        null=True,
        help_text="Purchase Order Number",
    )
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name="purchase_orders")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=DRAFT)

    # Order details
    order_date = models.DateTimeField(auto_now_add=True)
    expected_delivery_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    # Supplier-side reference numbers (filled in after creation, e.g. once the
    # supplier confirms the order and assigns their own identifiers)
    supplier_order_number = models.CharField(
        max_length=128,
        blank=True,
        help_text="Order number assigned by the supplier (e.g. their internal order ID)",
    )
    sales_order_number = models.CharField(
        max_length=128,
        blank=True,
        help_text="Sales order number associated with this purchase order",
    )

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
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_orders",
    )
    sent_at = models.DateTimeField(null=True, blank=True)

    # Void tracking (for orphaned/rejected POs)
    voided_at = models.DateTimeField(
        null=True, blank=True, help_text="When this purchase order was voided"
    )
    voided_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="voided_purchase_orders",
        help_text="User who voided this purchase order",
    )
    void_reason = models.TextField(blank=True, help_text="Reason for voiding this purchase order")

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
        """Total number of distinct non-voided items in this order."""
        return sum(1 for item in self.items.all() if not item.is_voided)

    @property
    def total_quantity(self) -> int:
        """Total quantity of non-voided items ordered."""
        return sum(
            item.quantity_ordered
            for item in self.items.all()
            if not item.is_voided and item.quantity_ordered is not None
        )

    @property
    def effective_estimated_total(self) -> Decimal:
        """Estimated total cost excluding voided line items.

        Voided lines are subtracted from the stored ``estimated_total`` so the
        displayed cost reflects only items the supplier is actually fulfilling.
        """
        voided_total = sum(
            (item.estimated_cost for item in self.items.all() if item.is_voided),
            start=Decimal("0.00"),
        )
        base = self.estimated_total or Decimal("0.00")
        adjusted = base - voided_total
        if adjusted < Decimal("0.00"):
            return Decimal("0.00")
        return adjusted

    @property
    def has_active_items(self) -> bool:
        """Whether any line item on this PO is not voided."""
        return any(not item.is_voided for item in self.items.all())

    @property
    def total_received_quantity(self) -> int:
        """Total quantity of all items received."""
        return sum(
            item.quantity_received
            for item in self.items.all()
            if item.quantity_received is not None
        )

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
        total = sum((item.estimated_cost for item in self.items.all()), start=Decimal("0.00"))
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

    def save(self, *args, **kwargs) -> None:
        """Ensure a PO number exists before saving.

        Concurrent creates can read the same "last" po_number and race to insert
        duplicates; retry with a fresh number on uniqueness collisions.
        """
        if self.po_number and self.pk:
            super().save(*args, **kwargs)
            return

        auto_assigned = not self.po_number
        max_retries = 5
        for attempt in range(max_retries):
            if auto_assigned:
                self.po_number = None
                self.auto_generate_po_number()
            try:
                with transaction.atomic():
                    super().save(*args, **kwargs)
                return
            except IntegrityError:
                if not auto_assigned or attempt == max_retries - 1:
                    raise


class PurchaseOrderItem(models.Model):
    """
    Line item within a purchase order.

    Represents a specific item or asset ordered from a supplier.
    Tracks received quantities and costs.

    Can be one of:
    - An inventory item (via item_supplier)
    - An asset (via asset)
    - A freeform item (via description, when neither item_supplier nor asset is set)
    """

    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name="items"
    )
    item_supplier = models.ForeignKey(
        ItemSupplier,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Specific supplier relationship for inventory items",
    )
    asset = models.ForeignKey(
        "inventory.Asset",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="purchase_order_items",
        help_text="Asset being purchased (for asset purchases)",
    )
    description = models.CharField(
        max_length=500,
        blank=True,
        help_text="Freeform description for line items not linked to inventory items or assets",
    )

    # Order quantities
    quantity_ordered = models.PositiveIntegerField(
        validators=[MinValueValidator(1)], help_text="Quantity ordered from supplier"
    )
    quantity_received = models.PositiveIntegerField(
        default=0, help_text="Quantity actually received"
    )
    order_in_packages = models.PositiveIntegerField(
        default=0,
        help_text="Number of packages ordered (calculated from quantity_ordered / quantity_per_package for inventory items)",
    )

    # Pricing
    unit_cost_ordered = models.DecimalField(
        max_digits=10, decimal_places=4, help_text="Unit cost at time of ordering"
    )
    unit_cost_actual = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Actual unit cost charged",
    )

    # Shipment tracking
    expected_shipment_date = models.DateField(
        null=True,
        blank=True,
        help_text="Expected shipment date for this line item (useful for items with longer lead times)",
    )

    # Status
    is_voided = models.BooleanField(
        default=False,
        help_text="Whether this line item has been voided (e.g., item discontinued by supplier)",
    )
    voided_at = models.DateTimeField(
        null=True, blank=True, help_text="When this line item was voided"
    )
    voided_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="voided_purchase_order_items",
        help_text="User who voided this line item",
    )
    void_reason = models.TextField(blank=True, help_text="Reason for voiding this line item")

    # Notes
    notes = models.TextField(blank=True)

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["purchase_order", "item_supplier__item__name", "asset__name"]
        unique_together = [
            ["purchase_order", "item_supplier"],
            ["purchase_order", "asset"],
        ]
        indexes = [
            models.Index(fields=["purchase_order", "item_supplier"]),
            models.Index(fields=["purchase_order", "asset"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(item_supplier__isnull=False, asset__isnull=True)
                    | models.Q(item_supplier__isnull=True, asset__isnull=False)
                    | (
                        models.Q(item_supplier__isnull=True, asset__isnull=True)
                        & ~models.Q(description="")
                    )
                ),
                name="purchase_order_item_must_have_item_or_asset",
            ),
        ]

    def __str__(self) -> str:
        if self.item_supplier:
            return f"{self.item_supplier.item.name} - {self.quantity_ordered} units"
        elif self.asset:
            return f"{self.asset.name} - {self.quantity_ordered} units"
        return f"Purchase Order Item - {self.quantity_ordered} units"

    @property
    def item(self) -> Optional[InventoryItem]:
        """Convenience property to access the inventory item (if applicable)."""
        if self.item_supplier:
            return self.item_supplier.item
        return None

    @property
    def supplier(self) -> Optional[Supplier]:
        """Convenience property to access the supplier."""
        if self.item_supplier:
            return self.item_supplier.supplier
        elif self.asset and self.asset.manufacturer:
            return self.asset.manufacturer
        return None

    @property
    def estimated_cost(self) -> Decimal:
        """Calculate estimated total cost for this line item."""
        if self.quantity_ordered is None or self.unit_cost_ordered is None:
            return Decimal("0.00")
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
        if self.quantity_ordered is None:
            return False
        return self.quantity_received >= self.quantity_ordered

    @property
    def quantity_pending(self) -> int:
        """Calculate quantity still pending delivery."""
        if self.quantity_ordered is None:
            return 0
        return max(0, self.quantity_ordered - self.quantity_received)


class PurchaseOrderAttachment(models.Model):
    """
    A file attached to a purchase order after creation.

    Used to attach supporting documents (sales orders, supplier confirmations,
    receipts, etc.) once the PO is in flight.
    """

    purchase_order = models.ForeignKey(
        PurchaseOrder,
        on_delete=models.CASCADE,
        related_name="attachments",
        help_text="The purchase order this attachment belongs to",
    )
    file = models.FileField(
        upload_to="purchase_orders/attachments/%Y/%m/",
        help_text="Attached document",
    )
    description = models.CharField(max_length=500, blank=True)
    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_order_attachments",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at"]

    def __str__(self) -> str:
        return f"Attachment for PO #{self.purchase_order.po_number} ({self.uploaded_at.date()})"


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
        default=False,
        help_text="Mark as complete when all items in this delivery are processed",
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
        validators=[MinValueValidator(1)],
        help_text="Quantity actually received for this item",
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
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scanned_items",
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


class WebHook(models.Model):
    """
    Generic webhook configuration for event notifications.

    Webhooks allow external systems to be notified when specific events occur,
    such as reorder requests, low stock alerts, delivery notifications, etc.
    """

    # Event type choices
    REORDER_REQUEST_CREATED = "reorder_request_created"
    REORDER_REQUEST_APPROVED = "reorder_request_approved"
    REORDER_REQUEST_ORDERED = "reorder_request_ordered"
    REORDER_REQUEST_RECEIVED = "reorder_request_received"
    ITEM_LOW_STOCK = "item_low_stock"
    PURCHASE_ORDER_CREATED = "purchase_order_created"
    DELIVERY_RECEIVED = "delivery_received"
    FIXTURE_REFILL_REQUESTED = "fixture_refill_requested"
    LOCATION_CHECKIN = "location_checkin"
    LOCATION_FEEDBACK = "location_feedback"
    SECURITY_REPORT = "security_report"
    LOCATION_PROBLEM_REPORTED = "location_problem_reported"

    EVENT_TYPE_CHOICES = [
        (REORDER_REQUEST_CREATED, "Reorder Request Created"),
        (REORDER_REQUEST_APPROVED, "Reorder Request Approved"),
        (REORDER_REQUEST_ORDERED, "Reorder Request Ordered"),
        (REORDER_REQUEST_RECEIVED, "Reorder Request Received"),
        (ITEM_LOW_STOCK, "Item Low Stock"),
        (PURCHASE_ORDER_CREATED, "Purchase Order Created"),
        (DELIVERY_RECEIVED, "Delivery Received"),
        (FIXTURE_REFILL_REQUESTED, "Fixture Refill Requested"),
        (LOCATION_CHECKIN, "Location Check-in"),
        (LOCATION_FEEDBACK, "Location Feedback"),
        (SECURITY_REPORT, "Security Report"),
        (LOCATION_PROBLEM_REPORTED, "Location Problem Reported"),
    ]

    # Core fields
    name = models.CharField(max_length=200, help_text="Descriptive name for this webhook")
    url = models.URLField(help_text="Webhook endpoint URL to POST notifications to")
    event_type = models.CharField(
        max_length=50,
        choices=EVENT_TYPE_CHOICES,
        help_text="Type of event that triggers this webhook",
    )

    # Configuration
    is_active = models.BooleanField(
        default=True, help_text="Enable or disable this webhook without deleting it"
    )
    secret = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional secret key for HMAC signature verification",
    )
    headers = models.JSONField(
        default=dict,
        blank=True,
        help_text="Optional custom HTTP headers to send with webhook (as JSON object)",
    )

    # Metadata
    description = models.TextField(blank=True, help_text="Description of what this webhook does")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Statistics
    last_triggered_at = models.DateTimeField(
        null=True, blank=True, help_text="When this webhook was last triggered"
    )
    success_count = models.PositiveIntegerField(
        default=0, help_text="Number of successful webhook deliveries"
    )
    failure_count = models.PositiveIntegerField(
        default=0, help_text="Number of failed webhook deliveries"
    )
    last_error = models.TextField(
        blank=True, help_text="Last error message if webhook delivery failed"
    )

    class Meta:
        ordering = ["event_type", "name"]
        indexes = [
            models.Index(fields=["event_type", "is_active"]),
            models.Index(fields=["is_active", "-last_triggered_at"]),
        ]

    def __str__(self) -> str:
        status = "✓" if self.is_active else "✗"
        return f"{status} {self.name} ({self.get_event_type_display()})"

    @property
    def success_rate(self) -> float:
        """Calculate success rate percentage."""
        total = self.success_count + self.failure_count
        if total == 0:
            return 0.0
        return (self.success_count / total) * 100

    def record_success(self) -> None:
        """Record a successful webhook delivery."""
        self.success_count += 1
        self.last_triggered_at = timezone.now()
        self.last_error = ""
        self.save(update_fields=["success_count", "last_triggered_at", "last_error"])

    def record_failure(self, error_message: str) -> None:
        """Record a failed webhook delivery."""
        self.failure_count += 1
        self.last_triggered_at = timezone.now()
        self.last_error = error_message[:1000]  # Limit error message length
        self.save(update_fields=["failure_count", "last_triggered_at", "last_error"])


class PurchaseOrderAuditEvent(models.Model):
    """Append-only audit log for safety-critical purchase-order mutations.

    Captures actor, timestamp, affected entity (PO / line item / attachment),
    notes, and metadata for each meaningful state change. Rows are written
    by ``reorder_queue.audit.record_event`` and never updated or deleted by
    application code.

    Per gh #353 / #334. Pattern mirrors
    ``forgekey.models.ForgeKeyAuditEvent`` (gh #352) so the eventual unified
    review surface (gh #359) can join across domains cleanly.
    """

    ACTION_PO_CREATE = "po_create"
    ACTION_PO_VOID = "po_void"
    ACTION_PO_LINE_VOID = "po_line_void"
    ACTION_PO_MARK_DELIVERED = "po_mark_delivered"
    ACTION_ATTACHMENT_ADD = "attachment_add"
    ACTION_ATTACHMENT_REMOVE = "attachment_remove"

    ACTION_CHOICES = [
        (ACTION_PO_CREATE, "Purchase order created"),
        (ACTION_PO_VOID, "Purchase order voided"),
        (ACTION_PO_LINE_VOID, "Purchase order line item voided"),
        (ACTION_PO_MARK_DELIVERED, "Purchase order marked delivered"),
        (ACTION_ATTACHMENT_ADD, "Attachment added"),
        (ACTION_ATTACHMENT_REMOVE, "Attachment removed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_order_audit_actions",
        help_text="User who performed the action; null for system-initiated events.",
    )
    action = models.CharField(max_length=32, choices=ACTION_CHOICES)
    # Optional FKs to the entities involved. At least one is set per row;
    # SET_NULL on delete so the audit trail survives entity teardown.
    purchase_order = models.ForeignKey(
        "PurchaseOrder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    line_item = models.ForeignKey(
        "PurchaseOrderItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    attachment = models.ForeignKey(
        "PurchaseOrderAttachment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    notes = models.TextField(blank=True)
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Action-specific payload (delivery date, void reason, attachment filename, etc).",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["purchase_order", "-created_at"], name="po_audit_po_idx"),
            models.Index(fields=["actor", "-created_at"], name="po_audit_actor_idx"),
            models.Index(fields=["action", "-created_at"], name="po_audit_action_idx"),
        ]

    def __str__(self) -> str:
        target = self.purchase_order_id or self.line_item_id or self.attachment_id
        return f"{self.action} ({target}) @ {self.created_at:%Y-%m-%d %H:%M}"


class WebhookAuditEvent(models.Model):
    """Append-only audit log for webhook configuration + lifecycle events.

    Captures actor, action, webhook reference, notes, and per-action
    metadata (incl. delta for config-field changes). Rows are written by
    ``reorder_queue.webhook_audit.record_event`` and never updated or
    deleted by application code.

    Per gh #357 / #334. Mirrors the per-domain audit tables in #352-#356.

    Inbound auth failures and outbound delivery failures are intentionally
    NOT captured here — both are high-volume per-event and are summarized
    on the WebHook row itself (failure_count + last_error). A future
    aggregated-stat table can land separately if event-level capture
    becomes operationally necessary.
    """

    ACTION_WEBHOOK_CREATE = "webhook_create"
    ACTION_WEBHOOK_UPDATE = "webhook_update"
    ACTION_WEBHOOK_DELETE = "webhook_delete"
    ACTION_WEBHOOK_DISABLE = "webhook_disable"
    ACTION_WEBHOOK_ENABLE = "webhook_enable"
    ACTION_WEBHOOK_SECRET_ROTATE = "webhook_secret_rotate"

    ACTION_CHOICES = [
        (ACTION_WEBHOOK_CREATE, "Webhook created"),
        (ACTION_WEBHOOK_UPDATE, "Webhook config updated"),
        (ACTION_WEBHOOK_DELETE, "Webhook deleted"),
        (ACTION_WEBHOOK_DISABLE, "Webhook disabled"),
        (ACTION_WEBHOOK_ENABLE, "Webhook enabled"),
        (ACTION_WEBHOOK_SECRET_ROTATE, "Webhook secret rotated"),
    ]

    # Non-secret config fields the audit hook tracks for diff capture.
    # ``secret`` is intentionally excluded — its value never appears in
    # audit metadata; only the fact that it changed is recorded via the
    # `webhook_secret_rotate` action.
    AUDITED_FIELDS = (
        "name",
        "url",
        "event_type",
        "is_active",
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="webhook_audit_actions",
        help_text="User who performed the action; null for system-initiated events.",
    )
    action = models.CharField(max_length=32, choices=ACTION_CHOICES)
    webhook = models.ForeignKey(
        WebHook,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    notes = models.TextField(blank=True)
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Action-specific payload. For webhook_update, includes a "
            "'changes' dict mapping field name -> {before, after}. The "
            "webhook secret is NEVER included — only the fact that it "
            "changed is recorded."
        ),
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["webhook", "-created_at"], name="webhook_audit_wh_idx"),
            models.Index(fields=["actor", "-created_at"], name="webhook_audit_actor_idx"),
            models.Index(fields=["action", "-created_at"], name="webhook_audit_action_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.action} ({self.webhook_id}) @ {self.created_at:%Y-%m-%d %H:%M}"
