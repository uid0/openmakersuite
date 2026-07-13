"""
Models for donation management.

Tracks bulk donations separately from Assets, with optional linking
when donated items become assets or inventory items.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone

User = get_user_model()


class Donation(models.Model):
    """
    Tracks a bulk donation receipt event.

    Represents a single donation event where multiple items were received
    from a donor. Items are tracked separately in DonationItem.
    """

    # Status choices
    PENDING = "pending"
    REVIEWED = "reviewed"
    PROCESSING = "processing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (PENDING, "Pending Review"),
        (REVIEWED, "Reviewed"),
        (PROCESSING, "Processing"),
        (COMPLETED, "Completed"),
        (CANCELLED, "Cancelled"),
    ]

    # Identification
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    donation_number = models.CharField(
        max_length=50,
        unique=True,
        blank=True,
        help_text="Unique donation reference number (auto-generated if not provided)",
    )

    # Donor information
    donor_name = models.CharField(
        max_length=200,
        help_text="Name of the donor (individual or organization)",
    )
    donor_email = models.EmailField(
        blank=True,
        help_text="Donor email address (if available)",
    )
    donor_phone = models.CharField(
        max_length=50,
        blank=True,
        help_text="Donor phone number (if available)",
    )
    donor_address = models.TextField(
        blank=True,
        help_text="Donor address (if available)",
    )

    # Receipt information
    date_received = models.DateField(
        default=timezone.now,
        help_text="Date the donation was received",
    )
    received_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="received_donations",
        help_text="User who received the donation",
    )
    received_notes = models.TextField(
        blank=True,
        help_text="Initial notes about the donation receipt",
    )

    # Status and processing
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=PENDING,
        help_text="Current status of the donation",
    )
    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_donations",
        help_text="User who reviewed the donation",
    )
    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the donation was reviewed",
    )
    review_notes = models.TextField(
        blank=True,
        help_text="Notes from the review process",
    )

    # Additional information
    estimated_number_of_items = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Estimated number of items in this donation (used to generate QR code labels)",
    )
    estimated_value = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Estimated value of the donation (for record-keeping)",
    )
    associated_costs = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00,
        help_text="Costs associated with this donation (e.g., transportation, handling)",
    )
    cost_notes = models.TextField(
        blank=True,
        help_text="Notes about associated costs (what they were for)",
    )
    tax_receipt_issued = models.BooleanField(
        default=False,
        help_text="Whether a tax receipt was issued to the donor",
    )
    tax_receipt_number = models.CharField(
        max_length=100,
        blank=True,
        help_text="Tax receipt number (if issued)",
    )
    tax_receipt_issued_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the tax receipt was issued",
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date_received", "-created_at"]
        indexes = [
            models.Index(fields=["status", "-date_received"]),
            models.Index(fields=["donor_name", "-date_received"]),
            models.Index(fields=["donation_number"]),
        ]

    def __str__(self) -> str:
        return f"Donation from {self.donor_name} ({self.date_received})"

    def save(self, *args, **kwargs):
        """Auto-generate donation number if not provided.

        The ``DON-YYYY-NNN`` composition is delegated to
        :func:`donations.services.numbering.next_donation_number` (gh #887); the
        received-year selection stays here since it reads ``self.date_received``.
        """
        if not self.donation_number:
            from .services.numbering import next_donation_number

            year = self.date_received.year if self.date_received else timezone.now().year
            self.donation_number = next_donation_number(year)
        super().save(*args, **kwargs)

    @property
    def total_items(self) -> int:
        """Total number of items in this donation."""
        return self.items.count()

    @property
    def total_quantity(self) -> int:
        """Total quantity of all items in this donation."""
        return sum(item.quantity for item in self.items.all())

    @property
    def items_processed(self) -> int:
        """Number of items that have been fully processed (have dispositions)."""
        return sum(
            1 for item in self.items.all() if item.dispositions.exists() and item.is_fully_disposed
        )

    @property
    def net_value(self) -> float:
        """Calculate net value (estimated value minus associated costs)."""
        value = float(self.estimated_value or 0)
        costs = float(self.associated_costs or 0)
        return value - costs


class DonationItem(models.Model):
    """
    Individual item within a donation.

    Tracks each item or group of items received in a donation.
    Can optionally link to Asset or InventoryItem if the item becomes one.
    """

    # Condition choices
    EXCELLENT = "excellent"
    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"
    UNUSABLE = "unusable"

    CONDITION_CHOICES = [
        (EXCELLENT, "Excellent"),
        (GOOD, "Good"),
        (FAIR, "Fair"),
        (POOR, "Poor"),
        (UNUSABLE, "Unusable"),
    ]

    # Status choices
    PENDING_REVIEW = "pending_review"
    USABLE = "usable"
    UNUSABLE_ITEM = "unusable"
    PROCESSING = "processing"
    DISPOSED = "disposed"

    STATUS_CHOICES = [
        (PENDING_REVIEW, "Pending Review"),
        (USABLE, "Usable"),
        (UNUSABLE_ITEM, "Unusable"),
        (PROCESSING, "Processing"),
        (DISPOSED, "Fully Disposed"),
    ]

    donation = models.ForeignKey(
        Donation,
        on_delete=models.CASCADE,
        related_name="items",
        help_text="The donation this item belongs to",
    )

    # Item description
    name = models.CharField(
        max_length=200,
        help_text="Name or description of the item",
    )
    description = models.TextField(
        blank=True,
        help_text="Detailed description of the item",
    )
    quantity = models.PositiveIntegerField(
        default=1,
        help_text="Quantity of this item",
    )

    # Condition and status
    condition = models.CharField(
        max_length=20,
        choices=CONDITION_CHOICES,
        default=GOOD,
        help_text="Condition of the item when received",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=PENDING_REVIEW,
        help_text="Current status of this item",
    )

    # Optional links to Assets or InventoryItems
    # These are set when the item becomes a tracked asset or inventory item
    asset = models.ForeignKey(
        "inventory.Asset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="donation_items",
        help_text="Link to Asset if this item became a tracked asset",
    )
    inventory_item = models.ForeignKey(
        "inventory.InventoryItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="donation_items",
        help_text="Link to InventoryItem if this item became inventory",
    )

    # QR Code tracking
    access_code = models.CharField(
        max_length=6,
        blank=True,
        unique=True,
        help_text="6-character code for QR code scanning (auto-generated)",
    )
    qr_code = models.ImageField(
        upload_to="donations/qr_codes/",
        blank=True,
        null=True,
        help_text="QR code image for this donation item",
    )

    # Notes
    notes = models.TextField(
        blank=True,
        help_text="Notes about this specific item",
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["donation", "name"]
        indexes = [
            models.Index(fields=["donation", "status"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} (x{self.quantity}) - {self.donation.donor_name}"

    @property
    def is_fully_disposed(self) -> bool:
        """Check if all quantity has been disposed."""
        total_disposed = sum(disp.quantity for disp in self.dispositions.all())
        return total_disposed >= self.quantity

    @property
    def remaining_quantity(self) -> int:
        """Calculate remaining quantity not yet disposed."""
        total_disposed = sum(disp.quantity for disp in self.dispositions.all())
        return max(0, self.quantity - total_disposed)


class Disposition(models.Model):
    """
    Tracks what happened to a donated item.

    Each disposition represents a decision/action taken on a donation item.
    Multiple dispositions can exist for a single item if it's disposed in parts.
    """

    # Disposition type choices
    KEPT = "kept"
    SOLD = "sold"
    AUCTIONED = "auctioned"
    DONATED_OUT = "donated_out"
    RECYCLED = "recycled"
    DISPOSED = "disposed"
    RETURNED = "returned"
    PARTED_OUT = "parted_out"
    OTHER = "other"

    DISPOSITION_CHOICES = [
        (KEPT, "Kept for Use"),
        (SOLD, "Sold Directly"),
        (AUCTIONED, "Auctioned"),
        (DONATED_OUT, "Donated to Another Organization"),
        (RECYCLED, "Recycled"),
        (DISPOSED, "Disposed/Trashed"),
        (RETURNED, "Returned to Donor"),
        (PARTED_OUT, "Parted Out for Components"),
        (OTHER, "Other"),
    ]

    # Sale method choices (for sold/auctioned items)
    DIRECT_SALE = "direct"
    AUCTION = "auction"

    SALE_METHOD_CHOICES = [
        (DIRECT_SALE, "Direct Sale"),
        (AUCTION, "Auction"),
    ]

    # Kept destination choices (for kept items)
    KEPT_MAKERSPACE = "makerspace"
    KEPT_SIG = "sig"

    KEPT_DESTINATION_CHOICES = [
        (KEPT_MAKERSPACE, "Kept for Makerspace"),
        (KEPT_SIG, "Given to Committee/SIG"),
    ]

    donation_item = models.ForeignKey(
        DonationItem,
        on_delete=models.CASCADE,
        related_name="dispositions",
        help_text="The donation item this disposition applies to",
    )

    # Disposition details
    disposition_type = models.CharField(
        max_length=20,
        choices=DISPOSITION_CHOICES,
        help_text="What happened to this item",
    )
    quantity = models.PositiveIntegerField(
        default=1,
        help_text="Quantity disposed in this action",
    )
    disposition_date = models.DateField(
        default=timezone.now,
        help_text="Date the disposition occurred",
    )
    disposed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dispositions",
        help_text="User who performed the disposition",
    )

    # Sale information (for sold/auctioned items)
    sale_method = models.CharField(
        max_length=20,
        choices=SALE_METHOD_CHOICES,
        blank=True,
        help_text="Sale method (direct sale or auction) - required if sold/auctioned",
    )
    sale_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Sale price (if sold or auctioned)",
    )

    # Kept item information
    kept_destination = models.CharField(
        max_length=20,
        choices=KEPT_DESTINATION_CHOICES,
        blank=True,
        help_text="Where kept item goes (makerspace or SIG) - required if kept",
    )
    kept_for_sig = models.ForeignKey(
        "auth.Group",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="donation_dispositions",
        help_text="SIG/Committee receiving the item (if kept_for_sig)",
    )

    # Additional details
    notes = models.TextField(
        blank=True,
        help_text="Notes about this disposition",
    )
    recipient_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Recipient name (if donated out or sold)",
    )

    # Optional link to Asset if this disposition created one
    # This is useful when an item is kept and becomes a tracked asset
    created_asset = models.ForeignKey(
        "inventory.Asset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="disposition_origin",
        help_text="Asset created from this disposition (if applicable)",
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-disposition_date", "-created_at"]
        indexes = [
            models.Index(fields=["donation_item", "-disposition_date"]),
            models.Index(fields=["disposition_type", "-disposition_date"]),
        ]

    def __str__(self) -> str:
        return f"{self.disposition_type.title()} - {self.donation_item.name} (x{self.quantity})"


class TaxReceipt(models.Model):
    """
    Tax receipt (501(c)(3) donation certificate) for a donation.

    Each receipt has a unique UUID-based serial number for tracking
    and can be generated as a clean original or watermarked copy.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    donation = models.ForeignKey(
        Donation,
        on_delete=models.CASCADE,
        related_name="tax_receipts",
        help_text="The donation this receipt is for",
    )
    serial_number = models.UUIDField(
        unique=True,
        default=uuid.uuid4,
        editable=False,
        help_text="Unique serial number for this tax receipt",
    )
    issued_date = models.DateField(
        default=timezone.now,
        help_text="Date the receipt was issued",
    )
    issued_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_tax_receipts",
        help_text="User who issued this receipt",
    )
    pdf_file = models.FileField(
        upload_to="donations/tax_receipts/",
        blank=True,
        null=True,
        help_text="Generated PDF file for this receipt",
    )
    is_copy = models.BooleanField(
        default=False,
        help_text="Whether this is a copy/reprint (watermarked)",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-issued_date", "-created_at"]
        indexes = [
            models.Index(fields=["serial_number"]),
            models.Index(fields=["donation", "-issued_date"]),
        ]

    def __str__(self) -> str:
        copy_text = " (Copy)" if self.is_copy else ""
        return f"Tax Receipt {self.serial_number} - {self.donation.donor_name}{copy_text}"


class DonationAuditEvent(models.Model):
    """Append-only audit log for donation, tax-receipt, and disposition actions.

    Captures actor, timestamp, affected entity, notes, and metadata for each
    safety- or financial-relevant mutation. Rows are written by
    ``donations.audit.record_event`` and never updated or deleted by
    application code.

    Per gh #354 / #334. Pattern mirrors
    ``forgekey.ForgeKeyAuditEvent`` (gh #352) and
    ``reorder_queue.PurchaseOrderAuditEvent`` (gh #353) so the eventual
    unified review surface (gh #359) can join across domains cleanly.
    """

    ACTION_DONATION_CREATE = "donation_create"
    ACTION_TAX_RECEIPT_ISSUED = "tax_receipt_issued"
    ACTION_TAX_RECEIPT_REGENERATE = "tax_receipt_regenerate"
    ACTION_ITEM_DISPOSED = "item_disposed"

    ACTION_CHOICES = [
        (ACTION_DONATION_CREATE, "Donation created"),
        (ACTION_TAX_RECEIPT_ISSUED, "Tax receipt issued"),
        (ACTION_TAX_RECEIPT_REGENERATE, "Tax receipt regenerated"),
        (ACTION_ITEM_DISPOSED, "Donation item disposed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="donation_audit_actions",
        help_text="User who performed the action; null for system-initiated events.",
    )
    action = models.CharField(max_length=32, choices=ACTION_CHOICES)
    # Optional FKs to entities involved. At least one is set per row;
    # SET_NULL on delete so the audit trail survives entity teardown.
    donation = models.ForeignKey(
        Donation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    donation_item = models.ForeignKey(
        DonationItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    tax_receipt = models.ForeignKey(
        TaxReceipt,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    disposition = models.ForeignKey(
        Disposition,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    notes = models.TextField(blank=True)
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Action-specific payload (serial number, disposition method, etc).",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["donation", "-created_at"], name="don_audit_donation_idx"),
            models.Index(fields=["actor", "-created_at"], name="don_audit_actor_idx"),
            models.Index(fields=["action", "-created_at"], name="don_audit_action_idx"),
        ]

    def __str__(self) -> str:
        target = (
            self.donation_id or self.donation_item_id or self.tax_receipt_id or self.disposition_id
        )
        return f"{self.action} ({target}) @ {self.created_at:%Y-%m-%d %H:%M}"
