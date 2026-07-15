"""Maintenance and work-order models."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Optional

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class MaintenanceItem(models.Model):
    """
    A recurring preventive maintenance (PM) task for a physical asset.

    Each item defines what needs to be done, how often, and what materials are needed.
    When a user scans an asset QR code, outstanding PM tasks are shown with
    step-by-step instructions.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(
        "Asset",
        on_delete=models.CASCADE,
        related_name="maintenance_items",
        help_text="The asset this maintenance task belongs to",
    )
    title = models.CharField(max_length=200, help_text="Brief title for this maintenance task")
    description = models.TextField(
        blank=True,
        help_text="Detailed description of why this maintenance is needed",
    )
    instructions = models.TextField(
        blank=True,
        help_text="Step-by-step instructions for performing the maintenance",
    )
    estimated_time_minutes = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Estimated time to complete in minutes",
    )
    estimated_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Estimated total cost for materials and labor",
    )
    interval_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="How often this task should be performed (in days). Null means one-time or as-needed.",
    )
    last_completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this task was last completed",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive tasks are hidden from the scan page",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["asset", "title"]
        indexes = [
            models.Index(fields=["asset", "is_active"], name="mi_asset_active_idx"),
            models.Index(fields=["last_completed_at"], name="mi_last_completed_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.asset.name} — {self.title}"

    @property
    def next_due_at(self) -> Optional[Any]:
        """Calculate when this task is next due based on interval and last completion."""
        if not self.interval_days:
            return None
        if self.last_completed_at:
            from datetime import timedelta

            return self.last_completed_at + timedelta(days=self.interval_days)
        return None

    @property
    def is_overdue(self) -> bool:
        """Return True if the task is past its due date or has never been completed.

        A task with interval_days set but no last_completed_at is considered overdue
        immediately — it needs to be done at least once before a schedule can begin.
        Tasks without interval_days (one-time or as-needed) are never considered overdue.
        """
        from django.utils import timezone

        if not self.interval_days:
            return False
        next_due = self.next_due_at
        if next_due is None:
            return True
        return timezone.now() >= next_due

    @property
    def days_overdue(self) -> Optional[int]:
        """Return how many days overdue the task is, or None if not overdue."""
        from django.utils import timezone

        if not self.is_overdue:
            return None
        next_due = self.next_due_at
        if next_due is None:
            return None
        delta = timezone.now() - next_due
        return max(0, delta.days)


class MaintenanceMaterial(models.Model):
    """
    A material or supply needed to complete a MaintenanceItem.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    maintenance_item = models.ForeignKey(
        "MaintenanceItem",
        on_delete=models.CASCADE,
        related_name="materials",
        help_text="The maintenance task that requires this material",
    )
    inventory_item = models.ForeignKey(
        "InventoryItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="maintenance_materials",
        help_text="Optional link to an inventory item for stock checking",
    )
    name = models.CharField(max_length=200, help_text="Name of the material or supply")
    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("1.00"),
        help_text="Quantity needed",
    )
    unit = models.CharField(
        max_length=50,
        blank=True,
        help_text="Unit of measurement (e.g., pieces, oz, ml, feet)",
    )
    estimated_cost_per_unit = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Estimated cost per unit",
    )
    location_hint = models.CharField(
        max_length=200,
        blank=True,
        help_text=(
            "Where to find this consumable when it isn't a tracked inventory "
            "item (e.g., 'Shop supply cabinet, bin 4')."
        ),
    )
    notes = models.TextField(blank=True, help_text="Notes about sourcing or usage")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.quantity} {self.unit})"

    @property
    def total_estimated_cost(self) -> Decimal:
        """Calculate total estimated cost for this material."""
        return self.quantity * self.estimated_cost_per_unit


class MaintenanceTool(models.Model):
    """A tool needed to perform a :class:`MaintenanceItem` — and where to find it.

    Distinct from :class:`MaintenanceMaterial` (consumables that get used up):
    a tool is gathered, used, and returned. The optional ``inventory_item``
    link resolves a real storage location + on-hand count automatically;
    ``location_hint`` is a free-text fallback ("Tool crib, drawer 3") for
    tools that aren't tracked as inventory. Surfaced on the e-paper
    scan-to-log work order so a maintainer knows what to grab — and where it
    lives — before starting.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    maintenance_item = models.ForeignKey(
        "MaintenanceItem",
        on_delete=models.CASCADE,
        related_name="tools",
        help_text="The maintenance task that requires this tool",
    )
    inventory_item = models.ForeignKey(
        "InventoryItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="maintenance_tools",
        help_text=(
            "Optional link to an inventory item so the tool's storage location "
            "and on-hand count resolve automatically."
        ),
    )
    name = models.CharField(max_length=200, help_text="Name of the tool")
    quantity = models.PositiveIntegerField(default=1, help_text="How many are needed")
    location_hint = models.CharField(
        max_length=200,
        blank=True,
        help_text=(
            "Where to find this tool when it isn't a tracked inventory item "
            "(e.g., 'Tool crib, drawer 3')."
        ),
    )
    is_required = models.BooleanField(
        default=True,
        help_text="Required tools are highlighted on the work order",
    )
    notes = models.TextField(blank=True, help_text="Notes about the tool or its use")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} (for {self.maintenance_item.title})"


class MaintenanceLog(models.Model):
    """
    A record of a completed maintenance task.

    Created when a user marks a MaintenanceItem as completed.
    Updates the MaintenanceItem's last_completed_at timestamp.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    maintenance_item = models.ForeignKey(
        "MaintenanceItem",
        on_delete=models.CASCADE,
        related_name="logs",
        help_text="The maintenance task that was completed",
    )
    # When the WO-completion path creates this log, point back at the
    # WorkOrder so the WO viewset can dedupe and the asset detail page
    # can hyperlink. Manually-entered logs (from the "Log maintenance"
    # button) leave this null.
    work_order = models.ForeignKey(
        "WorkOrder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="maintenance_logs",
        help_text="Source work order if this log was auto-written on WO completion.",
    )
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="maintenance_logs",
        help_text="The user who completed the task",
    )
    location = models.ForeignKey(
        "Location",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="maintenance_logs",
        help_text="Where the work was performed (defaults to the asset's location).",
    )
    completed_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When the task was completed",
    )
    time_spent_minutes = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Actual time spent in minutes",
    )
    cost_incurred = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Actual cost incurred for materials and labor",
    )
    notes = models.TextField(blank=True, help_text="Notes about what was done or observed")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-completed_at"]
        indexes = [
            models.Index(
                fields=["maintenance_item", "completed_at"],
                name="ml_item_completed_idx",
            ),
        ]

    def __str__(self) -> str:
        completed_by = self.completed_by.get_full_name() if self.completed_by else "Unknown"
        return f"{self.maintenance_item.title} — completed by {completed_by} at {self.completed_at}"


class MaintenanceLogPhoto(models.Model):
    """A photo of the work attached to a completed :class:`MaintenanceLog`.

    Lets a maintainer document what they worked on from the e-paper
    scan-to-log flow. Mirrors :class:`WorkOrderPhoto` / `AssetProblemPhoto`.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    maintenance_log = models.ForeignKey(
        "MaintenanceLog",
        on_delete=models.CASCADE,
        related_name="photos",
        help_text="The maintenance log this photo documents",
    )
    image = models.ImageField(
        upload_to="maintenance_log_photos/%Y/%m/",
        help_text="Photo of the work performed",
    )
    caption = models.CharField(max_length=500, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="maintenance_log_photos",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self) -> str:
        return f"Photo for log {self.maintenance_log_id} ({self.uploaded_at.date()})"


class MaintenanceTask(models.Model):
    """
    An ordered sub-task (line item) within a MaintenanceItem.

    Allows a single maintenance item to have a numbered checklist of steps,
    e.g., Task 1: "Disconnect Power", Task 2: "Lock Out Equipment".
    These appear on both printed work order forms and the digital interface.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    maintenance_item = models.ForeignKey(
        "MaintenanceItem",
        on_delete=models.CASCADE,
        related_name="tasks",
        help_text="The maintenance task this step belongs to",
    )
    order = models.PositiveIntegerField(
        default=0,
        help_text="Display order (lower numbers appear first)",
    )
    title = models.CharField(max_length=200, help_text="Short title for this step")
    description = models.TextField(blank=True, help_text="Detailed instructions for this step")
    is_required = models.BooleanField(
        default=True,
        help_text="Required steps must be completed before the work order can close",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "title"]
        indexes = [
            models.Index(fields=["maintenance_item", "order"], name="mt_item_order_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.maintenance_item.title} — Step {self.order}: {self.title}"


class WorkOrder(models.Model):
    """
    A scheduled or generated work order for a preventive maintenance item.

    Work orders can be printed as PDF forms for non-technical users or
    completed digitally by technical users. Both paths feed back into the
    maintenance history.
    """

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In Progress"
        BLOCKED = "blocked", "Blocked"
        COMPLETED = "completed", "Completed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    maintenance_item = models.ForeignKey(
        "MaintenanceItem",
        on_delete=models.CASCADE,
        related_name="work_orders",
        help_text="The maintenance task this work order is for",
    )
    # Bundled sibling PMs on the same asset that were due around the
    # same time and got rolled into this work order via the auto-bundle
    # window (PM_AUTO_BUNDLE_DUE_WITHIN_DAYS). The primary
    # ``maintenance_item`` above is the row the WO was originally
    # generated for; everything else due on the same asset within the
    # window gets attached here so a maker can do them all in one
    # trip and close them with per-item checkboxes.
    additional_maintenance_items = models.ManyToManyField(
        "MaintenanceItem",
        blank=True,
        related_name="bundled_work_orders",
        help_text=(
            "Sibling PMs on the same asset bundled into this WO via auto-bundling. "
            "When the WO is marked completed, every linked item (primary + bundled) "
            "gets its last_completed_at advanced and a MaintenanceLog row written."
        ),
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
        help_text="Current status of this work order",
    )
    due_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date by which this work order should be completed",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_work_orders",
        help_text="User assigned to complete this work order",
    )
    completed_by_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Name of person who completed the work (for paper form tracking)",
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this work order was marked complete",
    )
    notes = models.TextField(blank=True, help_text="Notes about this work order")
    estimated_external_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=(
            "Counterfactual cost of having this work performed by an outside vendor. "
            "Used for ROI reporting (value generated by completing in-house). Leave "
            "blank for routine work where the estimate isn't meaningful."
        ),
    )
    completed_scan = models.FileField(
        upload_to="work_orders/scans/%Y/%m/",
        null=True,
        blank=True,
        help_text=(
            "Completed work order PDF (attached when a paper form is scanned/emailed in "
            "via the Postmark inbound webhook)."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["maintenance_item", "status"], name="wo_item_status_idx"),
            models.Index(fields=["due_date", "status"], name="wo_due_status_idx"),
            models.Index(fields=["status", "-created_at"], name="wo_status_created_idx"),
        ]

    def __str__(self) -> str:
        return f"WO-{str(self.id)[:8].upper()} — {self.maintenance_item.title} ({self.get_status_display()})"

    @property
    def is_overdue(self) -> bool:
        """Return True if this open work order is past its due date."""
        from django.utils import timezone

        if self.status == self.Status.COMPLETED:
            return False
        if not self.due_date:
            return False
        return timezone.now().date() > self.due_date

    @property
    def short_id(self) -> str:
        """Return a short human-readable identifier for this work order."""
        return f"WO-{str(self.id)[:8].upper()}"


class WorkOrderTaskCompletion(models.Model):
    """
    Tracks completion of an individual task step within a work order.

    Created for each MaintenanceTask when a WorkOrder is generated,
    allowing granular tracking of which steps have been completed.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_order = models.ForeignKey(
        "WorkOrder",
        on_delete=models.CASCADE,
        related_name="task_completions",
        help_text="The work order this task completion belongs to",
    )
    task = models.ForeignKey(
        "MaintenanceTask",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="completions",
        help_text="The task step (null if task was deleted after WO creation)",
    )
    task_title = models.CharField(
        max_length=200,
        help_text="Denormalized task title (preserved if task is later deleted)",
    )
    task_order = models.PositiveIntegerField(
        default=0,
        help_text="Display order (denormalized from task at creation time)",
    )
    is_required = models.BooleanField(
        default=True,
        help_text="Whether this step is required",
    )
    is_completed = models.BooleanField(default=False)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="completed_work_order_tasks",
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["task_order", "task_title"]
        indexes = [
            models.Index(fields=["work_order", "is_completed"], name="wotc_wo_completed_idx"),
        ]

    def __str__(self) -> str:
        status = "✓" if self.is_completed else "○"
        return f"{status} {self.task_title} (WO {self.work_order.short_id})"


class WorkOrderMaterialUsage(models.Model):
    """
    Tracks which materials were actually used when completing a work order.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_order = models.ForeignKey(
        "WorkOrder",
        on_delete=models.CASCADE,
        related_name="material_usage",
        help_text="The work order this material usage belongs to",
    )
    material = models.ForeignKey(
        "MaintenanceMaterial",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="usage_records",
        help_text="The material specification (null if deleted after WO creation)",
    )
    material_name = models.CharField(
        max_length=200,
        help_text="Denormalized material name",
    )
    quantity_planned = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("1.00"),
        help_text="Planned quantity from the maintenance item spec",
    )
    quantity_used = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("1.00"),
        help_text=(
            "Quantity actually consumed. When this row is marked used and the "
            "material links to an inventory item, the item's stock is decremented "
            "by this amount (rounded to whole stock units) and a UsageLog row is "
            "written. Defaults to the planned quantity."
        ),
    )
    unit = models.CharField(max_length=50, blank=True)
    was_used = models.BooleanField(
        default=False,
        help_text="Whether this material was actually used",
    )
    # --- Inventory-decrement tracking (PR3, op-uh8z) -------------------------
    # ``applied_quantity`` is the idempotency guard AND the exact amount to
    # restore on reversal: it holds the whole number of stock units decremented
    # from the linked inventory item, or ``None`` when no decrement is currently
    # applied (fresh row, reversed row, or a flag-only material with no inventory
    # link). ``usage_log`` points at the UsageLog written for the decrement so it
    # can be voided (deleted) when the row is un-used.
    applied_quantity = models.IntegerField(
        null=True,
        blank=True,
        default=None,
        help_text=(
            "Whole stock units decremented from the linked inventory item for "
            "this usage; null when no decrement is currently applied. Reversal "
            "restores exactly this amount."
        ),
    )
    usage_log = models.ForeignKey(
        "UsageLog",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="UsageLog row written when stock was decremented; voided on reversal.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["material_name"]

    def __str__(self) -> str:
        status = "used" if self.was_used else "not used"
        return f"{self.material_name} ({status}) — WO {self.work_order.short_id}"

    @property
    def stock_applied(self) -> bool:
        """True when a stock decrement is currently applied for this usage."""
        return self.applied_quantity is not None


class WorkOrderPhoto(models.Model):
    """
    A photo attached to a work order by a technician.

    Used for documenting wear, damage, or completed work.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_order = models.ForeignKey(
        "WorkOrder",
        on_delete=models.CASCADE,
        related_name="photos",
        help_text="The work order this photo belongs to",
    )
    image = models.ImageField(
        upload_to="work_order_photos/%Y/%m/",
        help_text="Photo of the asset or work performed",
    )
    caption = models.CharField(max_length=500, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_order_photos",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self) -> str:
        return f"Photo for WO {self.work_order.short_id} ({self.uploaded_at.date()})"


class WorkOrderValidation(models.Model):
    """
    Audit record of a pre-finalization validation prompt acknowledgement.

    AC-3 (oms-2da): before a work order can transition to ``completed`` (or
    have a PDF generated for printing), the user must confirm the electrical
    info, LOTO requirements, and required-fields checklist. One row is
    created per acknowledgement; the latest row is what gates the finalize
    action and is what the audit trail surfaces.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_order = models.ForeignKey(
        "WorkOrder",
        on_delete=models.CASCADE,
        related_name="validations",
    )
    validated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_order_validations",
    )
    validated_at = models.DateTimeField(auto_now_add=True)
    electrical_acknowledged = models.BooleanField(default=False)
    loto_acknowledged = models.BooleanField(default=False)
    required_fields_acknowledged = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-validated_at"]
        indexes = [
            models.Index(fields=["work_order", "-validated_at"], name="wov_wo_validated_idx"),
        ]

    def __str__(self) -> str:
        return f"Validation for WO {self.work_order.short_id} @ {self.validated_at:%Y-%m-%d %H:%M}"

    @property
    def is_complete(self) -> bool:
        """All three acknowledgements present."""
        return (
            self.electrical_acknowledged
            and self.loto_acknowledged
            and self.required_fields_acknowledged
        )


class WorkOrderOmrTemplate(models.Model):
    """Persisted OMR region map for a printed scan-to-complete work-order form.

    When the OMR form variant is generated (``generate_work_order_omr_pdf``),
    the absolute page rect of every mark — each task/material checkbox plus the
    completion boxes (``work_complete``, ``result_pass``/``result_fail``) and
    the ink initials/date regions — is captured and normalized against the 4
    corner fiducials into ``regions_json``. bead-2's reader detects the
    fiducials in a *scanned* copy, warps into template space, and thresholds
    each region. The snapshot mirrors the ``parsed_fields`` convention on
    :class:`WorkOrderSubmission`: a JSON blob describing marks by target_id.

    **Template-drift guard.** ``template_version`` is a stable content
    signature of the WO's current task/material set at print time (see
    ``inventory.services.work_order_omr.compute_template_version``). Exactly
    one row is kept per work order (replaced on reprint). bead-2 recomputes the
    signature from the WO's *current* tasks and refuses a scan whose stored
    ``template_version`` no longer matches — i.e. the checklist was edited after
    the sheet was printed, so the physical box layout is stale.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_order = models.ForeignKey(
        "WorkOrder",
        on_delete=models.CASCADE,
        related_name="omr_templates",
        help_text="The work order this printed OMR form belongs to.",
    )
    template_version = models.PositiveIntegerField(
        help_text=(
            "Content signature of the WO's task/material set at print time. "
            "A scan is refused if this no longer matches the WO's current tasks."
        ),
    )
    page_w_pt = models.FloatField(help_text="Form page width in PDF points.")
    page_h_pt = models.FloatField(help_text="Form page height in PDF points.")
    fiducial_dict = models.CharField(
        max_length=32,
        default="aruco_4x4_50",
        help_text="cv2.aruco dictionary the 4 corner fiducials are drawn from.",
    )
    fiducials_json = models.JSONField(
        default=dict,
        blank=True,
        help_text="The 4 corner fiducial anchors: {corner: {id, cx, cy}} in points.",
    )
    regions_json = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Per-mark region map: list of {target_id, kind, page, rect_norm}. "
            "rect_norm is [x0,y0,x1,y1] normalized against the fiducial centers."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # One live template per work order — a reprint replaces the snapshot
            # (Template-drift guard keys off the single current row).
            models.UniqueConstraint(
                fields=["work_order"],
                name="unique_omr_template_per_work_order",
            ),
        ]

    def __str__(self) -> str:
        return f"OMR template v{self.template_version} — WO {self.work_order.short_id}"


class WorkOrderSubmission(models.Model):
    """
    An inbound, emailed copy of a completed work order PDF.

    Created when Postmark delivers an email with a PDF attachment to our inbound
    webhook. The PDF is parsed for its embedded Work Order ID and AcroForm
    checkbox values; on success, the corresponding WorkOrderTaskCompletion rows
    are marked complete and the PDF is attached to the WorkOrder's maintenance
    history.
    """

    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        APPLIED = "applied", "Applied"
        FAILED = "failed", "Failed"
        PENDING_REVIEW = "pending_review", "Pending review"

    class Source(models.TextChoices):
        EMAIL = "email", "Email"
        MANUAL = "manual", "Manual"
        SCAN = "scan", "Scan (OMR)"

    class Kind(models.TextChoices):
        PM_COMPLETION = "pm_completion", "PM completion"
        THIRD_PARTY_WO = "third_party_wo", "Third-party WO"
        LOCATION_PROBLEM = "location_problem", "Location Problem Report"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(
        max_length=20,
        choices=Kind.choices,
        default=Kind.PM_COMPLETION,
        help_text=(
            "Discriminates which ingest pipeline to run: pm_completion routes to "
            "WorkOrder updates; third_party_wo routes to ThirdPartyWorkOrder."
        ),
    )
    work_order = models.ForeignKey(
        "WorkOrder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submissions",
        help_text="Resolved PM work order (null for third_party_wo or until parsed)",
    )
    third_party_work_order = models.ForeignKey(
        "maintenance_orders.ThirdPartyWorkOrder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submissions",
        help_text="Resolved third-party work order (null for pm_completion or until parsed)",
    )
    location_problem = models.ForeignKey(
        "LocationProblem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submissions",
        help_text="Resolved location problem (set for location_problem ingestions)",
    )
    received_at = models.DateTimeField(auto_now_add=True)
    from_email = models.EmailField(blank=True)
    subject = models.CharField(max_length=500, blank=True)
    attachment = models.FileField(
        upload_to="work_orders/submissions/%Y/%m/",
        help_text="The raw PDF attachment as received from the email",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RECEIVED,
    )
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.EMAIL,
        help_text="How this submission entered the system (email webhook vs manual upload)",
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_order_submissions",
        help_text="User who manually uploaded the PDF (null for email-source submissions)",
    )
    parse_error = models.TextField(blank=True)
    parsed_fields = models.JSONField(
        default=dict,
        blank=True,
        help_text="Snapshot of checkbox values extracted from the PDF",
    )
    pending_changes = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "AC-4 (oms-2da): list of CV detections whose confidence is below "
            "the auto-apply threshold. Each entry: "
            "{kind, target_id, value, confidence, label}. Reviewer accepts or "
            "rejects via the work-order-submissions endpoints."
        ),
    )
    postmark_message_id = models.CharField(
        max_length=200,
        blank=True,
        db_index=True,
        help_text="Postmark MessageID header; used for idempotency",
    )

    class Meta:
        ordering = ["-received_at"]
        indexes = [
            models.Index(fields=["status", "-received_at"], name="wos_status_received_idx"),
        ]

    def __str__(self) -> str:
        wo = self.work_order.short_id if self.work_order else "unresolved"
        return f"WorkOrderSubmission({wo}) from {self.from_email or 'unknown'} [{self.status}]"


class MaintenanceAuditEvent(models.Model):
    """Append-only audit log for preventive-maintenance work-order and
    location-problem mutations not already covered by
    ``maintenance_orders.ThirdPartyWorkOrderAuditLog``.

    Per gh #355 / #334. Pattern mirrors the per-domain audit tables in
    forgekey (#352), reorder_queue (#353), and donations (#354) so the
    eventual unified review surface (#359) can join cleanly.
    """

    class Action(models.TextChoices):
        WO_CREATE = "wo_create", "Work order created"
        WO_COMPLETE = "wo_complete", "Work order completed"
        LOCATION_PROBLEM_RESOLVE = "location_problem_resolve", "Location problem resolved"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="maintenance_audit_actions",
        help_text="User who performed the action; null for system-initiated events.",
    )
    action = models.CharField(max_length=32, choices=Action.choices)
    work_order = models.ForeignKey(
        "WorkOrder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    location_problem = models.ForeignKey(
        "LocationProblem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    notes = models.TextField(blank=True)
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Action-specific payload (status transition, severity, etc).",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["work_order", "-created_at"], name="maint_audit_wo_idx"),
            models.Index(fields=["location_problem", "-created_at"], name="maint_audit_lp_idx"),
            models.Index(fields=["actor", "-created_at"], name="maint_audit_actor_idx"),
            models.Index(fields=["action", "-created_at"], name="maint_audit_action_idx"),
        ]

    def __str__(self) -> str:
        target = self.work_order_id or self.location_problem_id
        return f"{self.action} ({target}) @ {self.created_at:%Y-%m-%d %H:%M}"


class MaintenanceRecord(models.Model):
    """Backdated or recent maintenance event recorded against an asset.

    Captures historical PM/maintenance work that pre-dates OMS tracking
    (years of vendor service on HVAC, compressors, etc.) or recent jobs
    we want logged without running the full ThirdPartyWorkOrder workflow.
    Kept separate from ThirdPartyWorkOrder so the TPWO state machine,
    par-cost buffers, warranty gates, and audit log stay clean.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(
        "Asset",
        on_delete=models.PROTECT,
        related_name="maintenance_records",
        help_text="Asset the maintenance was performed on",
    )
    title = models.CharField(
        max_length=200,
        help_text="Short label, e.g. 'Annual HVAC service'",
    )
    description = models.TextField(help_text="Description of the work performed")
    completed_on = models.DateField(
        db_index=True,
        help_text="Calendar date the work was completed",
    )
    vendor = models.ForeignKey(
        "vendors.Vendor",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="maintenance_records",
        help_text="Vendor that performed the work (outsourced)",
    )
    performed_by_internal = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="internal_maintenance_records",
        help_text="Internal staff member who performed the work",
    )
    cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Total cost of the work; null when unknown or N/A",
    )
    invoice_number = models.CharField(max_length=64, blank=True)
    attachment = models.FileField(
        upload_to="inventory/maintenance_records/",
        null=True,
        blank=True,
        help_text="Invoice PDF, receipt photo, etc.",
    )
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_maintenance_records",
        help_text="User who entered this record",
    )
    recorded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-completed_on", "-recorded_at"]
        indexes = [
            models.Index(fields=["asset", "-completed_on"], name="maint_rec_asset_done_idx"),
            models.Index(fields=["vendor", "-completed_on"], name="maint_rec_vendor_done_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.completed_on}) on {self.asset_id}"

    def clean(self) -> None:
        super().clean()
        if self.vendor_id is None and self.performed_by_internal_id is None:
            raise ValidationError(
                {
                    "performed_by_internal": (
                        "Either a vendor or an internal staff member must be set."
                    )
                }
            )
        if self.completed_on is not None and self.completed_on > timezone.localdate():
            raise ValidationError({"completed_on": "completed_on cannot be in the future."})
