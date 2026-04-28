"""
Third-party maintenance work order models (Phase 1: foundation).

Phase 1 only ships the data structures + minimal CRUD. The 7-step state
machine, warranty/compliance gates, variance reconciliation, paper-form
ingestion, and SIG closure notifications all land in subsequent phases.
"""

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class ThirdPartyWorkOrder(models.Model):
    """A work order issued to a third-party vendor for asset maintenance."""

    WORK_TYPE_STANDARD = "standard"
    WORK_TYPE_MAJOR_REPAIR = "major_repair"
    WORK_TYPE_BUILDOUT = "buildout"
    WORK_TYPE_BUILDING_EMERGENCY = "building_emergency"

    WORK_TYPE_CHOICES = [
        (WORK_TYPE_STANDARD, "Standard"),
        (WORK_TYPE_MAJOR_REPAIR, "Major Repair"),
        (WORK_TYPE_BUILDOUT, "Buildout"),
        (WORK_TYPE_BUILDING_EMERGENCY, "Building Emergency"),
    ]

    STATUS_REQUESTED = "requested"
    STATUS_SOURCING = "sourcing"
    STATUS_SCHEDULED = "scheduled"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_VALIDATED = "validated"
    STATUS_FINANCIAL_REVIEW = "financial_review"
    STATUS_CLOSED = "closed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_REQUESTED, "Requested"),
        (STATUS_SOURCING, "Sourcing"),
        (STATUS_SCHEDULED, "Scheduled"),
        (STATUS_IN_PROGRESS, "In Progress"),
        (STATUS_VALIDATED, "Validated"),
        (STATUS_FINANCIAL_REVIEW, "Financial Review"),
        (STATUS_CLOSED, "Closed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(
        max_length=200,
        help_text="Short summary of the work to be performed",
    )

    # Asset linkage. Required for SIG work; nullable for Building Emergency
    # where Logistics may dispatch before an asset is identified.
    asset = models.ForeignKey(
        "inventory.Asset",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="third_party_work_orders",
        help_text=(
            "Primary asset this work order targets. Required for SIG work; "
            "may be null for Building Emergency. Multi-asset distribution is "
            "captured via ThirdPartyWorkOrderAsset."
        ),
    )

    vendor = models.ForeignKey(
        "vendors.Vendor",
        on_delete=models.PROTECT,
        related_name="work_orders",
        help_text="Vendor performing the work",
    )

    work_type = models.CharField(
        max_length=32,
        choices=WORK_TYPE_CHOICES,
        default=WORK_TYPE_STANDARD,
    )
    is_emergency = models.BooleanField(
        default=False,
        help_text=(
            "Denormalized fast-path flag for Building Emergency dispatch. "
            "Bypasses standard NTE approval and 3-quote sourcing."
        ),
    )

    status = models.CharField(
        max_length=32,
        choices=STATUS_CHOICES,
        default=STATUS_REQUESTED,
    )

    nte_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Not-To-Exceed amount approved by SIG. Null for emergency 'blank check' work.",
    )
    par_cost_buffer = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text=(
            "Absolute par-cost variance auto-approved without re-review. "
            "Phase 3 reconciliation also enforces a 15%-of-NTE relative cap."
        ),
    )
    actual_invoice_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Final invoiced total. Set during Step 6 financial reconciliation.",
    )
    dispatch_fee = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text=(
            "Flat truck-roll / dispatch fee. Auto-split equally across all "
            "ThirdPartyWorkOrderAsset rows during reconciliation (Phase 3)."
        ),
    )

    downtime_start = models.DateTimeField(null=True, blank=True)
    downtime_end = models.DateTimeField(null=True, blank=True)
    keyfob_id = models.CharField(
        max_length=64,
        blank=True,
        help_text=(
            "Keyfob checked out to the vendor for site access. "
            "Phase 5 enforces return before closure."
        ),
    )

    warranty_recovery = models.BooleanField(
        default=False,
        help_text="Set by Phase 2 warranty gate when costs are recoverable under active warranty",
    )

    shadow_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shadowed_third_party_work_orders",
        help_text="Ops staff member shadowing the vendor on-site",
    )
    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="opened_third_party_work_orders",
    )
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    notes = models.TextField(blank=True, help_text="Customer/SIG-visible notes about the work")
    internal_notes = models.TextField(
        blank=True,
        help_text="Internal Ops notes — not surfaced to vendors or SIG dashboards",
    )

    assets = models.ManyToManyField(
        "inventory.Asset",
        through="ThirdPartyWorkOrderAsset",
        related_name="distributed_third_party_work_orders",
        blank=True,
        help_text="Assets among which costs are distributed for multi-asset WOs",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-opened_at"]
        indexes = [
            models.Index(fields=["status", "-opened_at"], name="tpwo_status_opened_idx"),
            models.Index(fields=["vendor", "status"], name="tpwo_vendor_status_idx"),
            models.Index(fields=["is_emergency", "status"], name="tpwo_emerg_status_idx"),
            models.Index(fields=["asset", "-opened_at"], name="tpwo_asset_opened_idx"),
        ]

    def __str__(self) -> str:
        return f"TPWO-{str(self.id)[:8].upper()} {self.title}"

    @property
    def short_id(self) -> str:
        return f"TPWO-{str(self.id)[:8].upper()}"

    @property
    def downtime_duration(self):
        """Total downtime as a timedelta, or None if not yet bounded."""
        if not self.downtime_start or not self.downtime_end:
            return None
        return self.downtime_end - self.downtime_start


class ThirdPartyWorkOrderAsset(models.Model):
    """M2M through-row distributing a multi-asset WO's cost across assets.

    `share_pct` rows for a single work order are expected to sum to 100, but
    the constraint is enforced at the API/service layer rather than the DB —
    rows are added incrementally as Ops identifies which assets the truck
    roll touched.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_order = models.ForeignKey(
        ThirdPartyWorkOrder,
        on_delete=models.CASCADE,
        related_name="asset_links",
    )
    asset = models.ForeignKey(
        "inventory.Asset",
        on_delete=models.PROTECT,
        related_name="third_party_work_order_links",
    )
    share_pct = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        help_text="Share of the WO's total cost allocated to this asset (0-100)",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["work_order", "-share_pct"]
        constraints = [
            models.UniqueConstraint(
                fields=["work_order", "asset"], name="tpwo_unique_asset_per_wo"
            ),
        ]
        indexes = [
            models.Index(fields=["work_order"], name="tpwo_link_wo_idx"),
            models.Index(fields=["asset"], name="tpwo_link_asset_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.work_order.short_id} → {self.asset_id} ({self.share_pct}%)"


def _attachment_upload_path(instance, filename: str) -> str:
    return f"third_party_work_orders/{instance.work_order_id}/{filename}"


class ThirdPartyWorkOrderAttachment(models.Model):
    """File attached to a third-party WO: invoice, FSR, photo, quote, etc."""

    KIND_INVOICE = "invoice"
    KIND_FSR = "fsr"
    KIND_PHOTO = "photo"
    KIND_QUOTE = "quote"
    KIND_PAPER_FORM = "paper_form"
    KIND_OTHER = "other"

    KIND_CHOICES = [
        (KIND_INVOICE, "Invoice"),
        (KIND_FSR, "Field Service Report"),
        (KIND_PHOTO, "Photo"),
        (KIND_QUOTE, "Quote"),
        (KIND_PAPER_FORM, "Paper Form"),
        (KIND_OTHER, "Other"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_order = models.ForeignKey(
        ThirdPartyWorkOrder,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    file = models.FileField(upload_to=_attachment_upload_path)
    kind = models.CharField(max_length=32, choices=KIND_CHOICES, default=KIND_OTHER)
    caption = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_third_party_wo_attachments",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]
        indexes = [
            models.Index(fields=["work_order", "kind"], name="tpwoa_wo_kind_idx"),
            models.Index(fields=["kind", "-uploaded_at"], name="tpwoa_kind_upload_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.work_order.short_id} {self.get_kind_display()}: {self.caption or self.file.name}"
