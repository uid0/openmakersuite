"""
Third-party maintenance work order models.

Phase 1 shipped the data structures + minimal CRUD. Phase 2 (this file's
warranty gate + AssetWarranty model + vendor compliance hooks) adds the
preconditions Logistics needs before sourcing — warranty recovery and
TDLR/COI status surfacing. The 7-step state machine, variance
reconciliation, paper-form ingestion, and SIG closure notifications still
land in later phases.
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
    total_downtime = models.DurationField(
        null=True,
        blank=True,
        help_text=(
            "Persisted total downtime computed at validation. Equals "
            "downtime_end - downtime_start once Ops signs off in Step 5."
        ),
    )
    keyfob_id = models.CharField(
        max_length=64,
        blank=True,
        help_text=(
            "Keyfob checked out to the vendor for site access. "
            "Phase 5 enforces return before closure."
        ),
    )
    keyfob_returned_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Set when the vendor returns the keyfob. Closure is blocked while "
            "keyfob_id is set but keyfob_returned_at is null."
        ),
    )
    keyfob_returned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="keyfob_returned_third_party_work_orders",
        help_text="User who recorded the keyfob return.",
    )

    nte_set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="nte_set_third_party_work_orders",
        help_text="SIG admin who set the NTE during Step 1 intake",
    )
    nte_set_at = models.DateTimeField(null=True, blank=True)

    emergency_authorized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="emergency_authorized_third_party_work_orders",
        help_text=(
            "Logistics user who authorized emergency bypass of NTE / 3-quote "
            "rules. Re-authorization required every 24h via "
            "EmergencyAuthorization audit rows."
        ),
    )
    emergency_authorized_at = models.DateTimeField(null=True, blank=True)

    quote_waiver_signed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_waived_third_party_work_orders",
        help_text=(
            "Authorized user who signed off on bypassing the 3-quote "
            "requirement. Persisted with reason + timestamp for audit."
        ),
    )
    quote_waiver_signed_at = models.DateTimeField(null=True, blank=True)
    quote_waiver_reason = models.TextField(
        blank=True,
        help_text="Justification for waiving the 3-quote requirement (audit log)",
    )

    variance_status = models.CharField(
        max_length=32,
        blank=True,
        default="",
        choices=[
            ("", "Not evaluated"),
            ("auto_approved", "Auto-approved within par buffer"),
            ("blocked", "Blocked — exceeds par/15% NTE"),
        ],
        help_text=(
            "Outcome of Step 6 financial reconciliation. Set when "
            "advance_to_financial_review is called. 'blocked' prevents closure."
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
    allocated_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text=(
            "Cost share materialized at reconciliation: "
            "(actual_invoice_total - dispatch_fee) * share_pct + "
            "dispatch_fee/N. Persisted on advance_to_financial_review."
        ),
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


class AssetWarranty(models.Model):
    """Manufacturer or third-party warranty coverage for an asset.

    Used by the Phase 2 warranty gate on third-party WO creation: if the asset
    has an active warranty when a WO is opened, ``warranty_recovery`` is set
    on the WO so Logistics knows to pursue the manufacturer for reimbursement
    before paying the vendor invoice out of operating budget.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(
        "inventory.Asset",
        on_delete=models.CASCADE,
        related_name="warranties",
    )
    install_date = models.DateField(
        help_text="Date the warranty coverage begins (typically install or purchase date)"
    )
    duration_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Duration of coverage in days. Either duration_days or end_date "
            "must be provided; the other is computed automatically."
        ),
    )
    end_date = models.DateField(
        null=True,
        blank=True,
        help_text=(
            "Computed from install_date + duration_days when blank. "
            "Set explicitly to override (e.g., extended warranty)."
        ),
    )
    provider = models.CharField(
        max_length=200,
        help_text="Warranty provider — usually the manufacturer or extended-warranty carrier",
    )
    policy_number = models.CharField(max_length=128, blank=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-end_date"]
        verbose_name_plural = "asset warranties"
        indexes = [
            models.Index(fields=["asset", "-end_date"], name="aw_asset_end_idx"),
            models.Index(fields=["end_date"], name="aw_end_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.provider} warranty for {self.asset_id} (ends {self.end_date})"

    def clean(self):
        from datetime import timedelta

        from django.core.exceptions import ValidationError

        if not self.duration_days and not self.end_date:
            raise ValidationError(
                "Provide either duration_days or end_date — one is required to bound coverage."
            )
        if self.duration_days and self.end_date:
            expected = self.install_date + timedelta(days=self.duration_days)
            if expected != self.end_date:
                raise ValidationError(
                    "duration_days and end_date disagree; provide only one and let the other compute."
                )

    def save(self, *args, **kwargs):
        from datetime import timedelta

        if self.duration_days and not self.end_date:
            self.end_date = self.install_date + timedelta(days=self.duration_days)
        elif self.end_date and not self.duration_days:
            self.duration_days = (self.end_date - self.install_date).days
        super().save(*args, **kwargs)

    @property
    def is_active(self) -> bool:
        """True if today's date is within the install_date..end_date window."""
        from django.utils import timezone

        if not self.end_date:
            return False
        today = timezone.now().date()
        return self.install_date <= today <= self.end_date


class ThirdPartyWorkOrderQuote(models.Model):
    """Vendor quote captured during Step 2 sourcing.

    Standard work orders require ≥3 quotes before advancing from sourcing
    to scheduled. The 3-quote rule can be waived by an authorized user
    (recorded on the parent WO via ``quote_waiver_*`` fields).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_order = models.ForeignKey(
        ThirdPartyWorkOrder,
        on_delete=models.CASCADE,
        related_name="quotes",
    )
    vendor = models.ForeignKey(
        "vendors.Vendor",
        on_delete=models.PROTECT,
        related_name="third_party_quotes",
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Quoted total in dollars",
    )
    notes = models.TextField(blank=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_third_party_quotes",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["work_order", "amount"]
        indexes = [
            models.Index(fields=["work_order"], name="tpwo_quote_wo_idx"),
            models.Index(fields=["vendor"], name="tpwo_quote_vendor_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.work_order.short_id} quote {self.vendor.name}: ${self.amount}"


class EmergencyAuthorization(models.Model):
    """Audit row for Logistics emergency-bypass authorizations.

    Per the bead, Logistics' emergency authority must auto-revalidate every 24h.
    Each row represents a single authorization window. ``is_currently_valid``
    asks: was the most recent row created within the last 24 hours?
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_order = models.ForeignKey(
        ThirdPartyWorkOrder,
        on_delete=models.CASCADE,
        related_name="emergency_authorizations",
    )
    authorized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="emergency_authorizations",
    )
    authorized_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(
        help_text="24 hours after authorized_at unless explicitly revoked"
    )
    reason = models.TextField(blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-authorized_at"]
        indexes = [
            models.Index(fields=["work_order", "-authorized_at"], name="tpwo_emerg_auth_idx"),
        ]

    def __str__(self) -> str:
        return f"Emergency auth {self.work_order.short_id} by {self.authorized_by_id}"

    def save(self, *args, **kwargs):
        from datetime import timedelta

        from django.utils import timezone

        if not self.expires_at:
            self.expires_at = (self.authorized_at or timezone.now()) + timedelta(hours=24)
        super().save(*args, **kwargs)

    @property
    def is_currently_valid(self) -> bool:
        from django.utils import timezone

        now = timezone.now()
        if self.revoked_at and self.revoked_at <= now:
            return False
        return self.expires_at > now


class ThirdPartyWorkOrderAuditLog(models.Model):
    """Append-only audit row for third-party WO state transitions and overrides.

    Phase 5 (D): every state transition, variance override, and emergency
    authorization validation is recorded here so Finance + Logistics have a
    permanent paper trail. Rows are never updated or deleted by application
    code — admin removal requires raw SQL.
    """

    ACTION_TRANSITION = "transition"
    ACTION_VARIANCE_OVERRIDE = "variance_override"
    ACTION_EMERGENCY_AUTH = "emergency_auth"
    ACTION_EMERGENCY_VALIDATION = "emergency_validation"
    ACTION_KEYFOB_RETURN = "keyfob_return"
    ACTION_QUOTE_WAIVER = "quote_waiver"

    ACTION_CHOICES = [
        (ACTION_TRANSITION, "State transition"),
        (ACTION_VARIANCE_OVERRIDE, "Variance override"),
        (ACTION_EMERGENCY_AUTH, "Emergency authorization"),
        (ACTION_EMERGENCY_VALIDATION, "Emergency validation"),
        (ACTION_KEYFOB_RETURN, "Keyfob return"),
        (ACTION_QUOTE_WAIVER, "Quote waiver"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_order = models.ForeignKey(
        ThirdPartyWorkOrder,
        on_delete=models.CASCADE,
        related_name="audit_logs",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="third_party_wo_audit_actions",
        help_text="User who performed the action; null for system events.",
    )
    action = models.CharField(max_length=32, choices=ACTION_CHOICES)
    old_state = models.CharField(max_length=32, blank=True)
    new_state = models.CharField(max_length=32, blank=True)
    notes = models.TextField(blank=True)
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Action-specific payload (variance amounts, auth ids, etc).",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["work_order", "-created_at"], name="tpwo_audit_wo_idx"),
            models.Index(fields=["action", "-created_at"], name="tpwo_audit_action_idx"),
        ]

    def __str__(self) -> str:
        if self.old_state or self.new_state:
            return f"{self.work_order_id} {self.action}: {self.old_state} → {self.new_state}"
        return f"{self.work_order_id} {self.action}"


class RecoveryTask(models.Model):
    """Logistics follow-up task to chase warranty recovery on a closed WO.

    Auto-created when a WO closes with ``warranty_recovery=True``. Logistics
    works the queue, contacts the manufacturer, and closes the task once the
    reimbursement is reconciled.
    """

    STATUS_OPEN = "open"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_RESOLVED = "resolved"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Open"),
        (STATUS_IN_PROGRESS, "In Progress"),
        (STATUS_RESOLVED, "Resolved"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_order = models.ForeignKey(
        ThirdPartyWorkOrder,
        on_delete=models.CASCADE,
        related_name="recovery_tasks",
    )
    vendor = models.ForeignKey(
        "vendors.Vendor",
        on_delete=models.PROTECT,
        related_name="recovery_tasks",
        null=True,
        blank=True,
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Recoverable amount — typically the WO's actual_invoice_total at closure.",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    status = models.CharField(
        max_length=32,
        choices=STATUS_CHOICES,
        default=STATUS_OPEN,
    )
    assigned_group = models.CharField(
        max_length=64,
        default="Logistics",
        help_text="Auth Group name responsible for working this task.",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_recovery_tasks",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"], name="rt_status_created_idx"),
            models.Index(fields=["assigned_group", "status"], name="rt_group_status_idx"),
            models.Index(fields=["work_order"], name="rt_wo_idx"),
        ]

    def __str__(self) -> str:
        return f"RecoveryTask {self.title} [{self.status}]"
