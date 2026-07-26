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
    reference_image = models.ImageField(
        upload_to="maintenance_task_reference/%Y/%m/",
        null=True,
        blank=True,
        help_text=(
            "Instructional photo for this step — 'here is what this should look "
            "like'. Defined once on the template, printed next to the step on the "
            "work-order form and shown on the digital work order. This is the "
            "reference half; the photo a tech takes while performing the work is "
            "an evidence WorkOrderPhoto linked to the step's completion row."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "title"]
        indexes = [
            models.Index(fields=["maintenance_item", "order"], name="mt_item_order_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.maintenance_item.title} — Step {self.order}: {self.title}"


class ElapsedTimerModel(models.Model):
    """Accumulator + current-segment stopwatch shared by work orders and steps.

    Two pieces of state rather than a single ``started_at``/``ended_at`` pair
    (the :class:`forgekey.models.DeviceUsage` shape):

    - ``elapsed_seconds`` — time already *committed*, i.e. every segment that
      has been paused.
    - ``timing_since`` — when the segment currently running began, or null.

    Live elapsed is therefore ``elapsed_seconds + (now - timing_since)``. The
    accumulator is what makes pause/resume work: a tech can walk away from a
    job mid-way, come back an hour later, and the clock picks up instead of
    counting the gap as work. The server owns all of it — a browser only ticks
    a display over these numbers — so the total survives a reload and reads the
    same from a second device.
    """

    elapsed_seconds = models.PositiveIntegerField(
        default=0,
        help_text=(
            "Committed time in seconds. Excludes the segment currently running "
            "— read the serializer's live value for the running total."
        ),
    )
    is_timing = models.BooleanField(
        default=False,
        help_text="Whether the stopwatch is running right now.",
    )
    timing_since = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Start of the segment currently running (null while paused).",
    )

    class Meta:
        abstract = True

    #: Fields ``start_timer`` / ``pause_timer`` may touch, for ``update_fields``.
    TIMER_FIELDS = ("elapsed_seconds", "is_timing", "timing_since")

    def live_elapsed_seconds(self, *, now=None) -> int:
        """Seconds on the clock, including any segment still running."""
        total = self.elapsed_seconds or 0
        if self.is_timing and self.timing_since:
            now = now or timezone.now()
            total += max(0, int((now - self.timing_since).total_seconds()))
        return total

    def start_timer(self, *, now=None) -> bool:
        """Open a segment. Idempotent — returns False if already running."""
        if self.is_timing:
            return False
        self.is_timing = True
        self.timing_since = now or timezone.now()
        return True

    def pause_timer(self, *, now=None) -> bool:
        """Commit the running segment. Idempotent — False if already paused."""
        if not self.is_timing:
            return False
        if self.timing_since:
            now = now or timezone.now()
            self.elapsed_seconds = (self.elapsed_seconds or 0) + max(
                0, int((now - self.timing_since).total_seconds())
            )
        self.is_timing = False
        self.timing_since = None
        return True


class WorkOrder(ElapsedTimerModel):
    """
    A scheduled or generated work order for a preventive maintenance item.

    Work orders can be printed as PDF forms for non-technical users or
    completed digitally by technical users. Both paths feed back into the
    maintenance history.

    Digitally-completed work orders also carry a stopwatch
    (:class:`ElapsedTimerModel`): the WO-level total is wall-time-on-job, and
    each :class:`WorkOrderTaskCompletion` keeps its own so actual step
    durations can be compared against
    :attr:`MaintenanceItem.estimated_time_minutes`.
    """

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In Progress"
        BLOCKED = "blocked", "Blocked"
        COMPLETED = "completed", "Completed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Optional: a *preventive* work order comes from a PM template, a
    # *corrective* one comes from a reported problem and has no template at
    # all. SET_NULL rather than CASCADE so deleting a retired PM template
    # keeps the history of the work that was actually done under it.
    maintenance_item = models.ForeignKey(
        "MaintenanceItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_orders",
        help_text=(
            "The PM template this work order is for. Null for corrective work "
            "orders, which carry no template — read ``asset`` for the machine."
        ),
    )
    # The machine being worked on. Nullable in the schema (an FK can't be
    # added non-null to an existing table), but populated on every row: when a
    # ``maintenance_item`` is given and this is blank, ``save()`` derives it.
    # Read this — never ``maintenance_item.asset`` — so corrective work orders
    # are not silently dropped.
    asset = models.ForeignKey(
        "inventory.Asset",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="work_orders",
        help_text="The asset this work order is for",
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
    started_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When work first started on this order (the first time the timer "
            "was started). Never reset by a later pause/resume."
        ),
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this work order was marked complete",
    )
    notes = models.TextField(blank=True, help_text="Notes about this work order")
    loto_completion_note = models.TextField(
        blank=True,
        help_text=(
            "Free-text lockout/tagout completion note recorded web-side. The "
            "structured per-energy-source boxes (WorkOrderLotoCompletion) are the "
            "OMR-readable half; this is the free-text half of 'LOTO = both'."
        ),
    )
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

    def save(self, *args, **kwargs):
        """Keep ``asset`` populated for every work order.

        The column is nullable so the FK could be added to an existing table,
        but the whole point of it is that downstream code can read
        ``work_order.asset`` unconditionally. A preventive work order is
        normally created with only ``maintenance_item=``, so derive the asset
        from the template here rather than asking every caller to pass both.
        """
        if self.asset_id is None and self.maintenance_item_id:
            self.asset_id = self.maintenance_item.asset_id
            if "update_fields" in kwargs and kwargs["update_fields"] is not None:
                kwargs["update_fields"] = list(kwargs["update_fields"]) + ["asset"]
        super().save(*args, **kwargs)

    @property
    def display_title(self) -> str:
        """What to call this work order on a screen, a report, or a form.

        Preventive work orders are named by their PM template. Corrective ones
        have no template, so they fall back to the reported problem and then to
        the asset itself — a work order always has *something* to be called.
        """
        if self.maintenance_item_id:
            return self.maintenance_item.title
        # Reverse FK, so read it through ``.all()`` rather than ``.first()``:
        # a caller that prefetched ``asset_problems`` pays no extra query.
        # Only template-less (corrective) work orders ever reach this line.
        problem = next(iter(self.asset_problems.all()), None)
        if problem is not None and problem.description:
            return problem.description[:60]
        if self.asset_id:
            return self.asset.name
        return self.short_id

    def __str__(self) -> str:
        return f"{self.short_id} — {self.display_title} ({self.get_status_display()})"

    @property
    def actual_material_cost(self) -> Decimal:
        """Real money spent on materials for this job (op-768w, op-4pzp).

        Sums :attr:`WorkOrderMaterialUsage.actual_cost` over the lines that cost
        the job money, which is two different tests for the two kinds of row:

        * **Ad-hoc** (``is_ad_hoc``) — typed in during the job: a freehand
          supply or an out-of-pocket buy. A priced one counts the moment it is
          added, ``was_used`` or not. That flag governs *stock*, and the money
          left the wallet at the hardware store whether or not anyone
          afterwards ticks a box about the shelf (op-4pzp).
        * **Template-derived** — a frozen copy of the PM spec, so it is a
          *plan* until someone marks it used. A planned-but-unused material
          still costs nothing.

        Lines with no recorded ``unit_cost`` contribute zero rather than
        blocking the total, so a partially-priced job still reports what is
        known.

        Reads ``material_usage.all()`` so a caller who prefetched it (every API
        read path does) pays no extra query. This is the job-cost roll-up the
        cost-recovery/TCO reports and the actual-vs-estimated display consume.
        The committee ledger charge deliberately does **not** — it books stock
        leaving the shelf, so it reads :attr:`consumed_material_cost`.
        """
        return sum(
            (
                usage.actual_cost
                for usage in self.material_usage.all()
                if (usage.was_used or usage.is_ad_hoc) and usage.actual_cost is not None
            ),
            Decimal("0.00"),
        )

    @property
    def consumed_material_cost(self) -> Decimal:
        """Priced material this job actually drew down — the ledger's basis.

        Only lines marked *used*, ad-hoc or not: the pre-op-4pzp reading of
        :attr:`actual_material_cost`, kept as a number of its own because the
        committee charge (``inventory.services.work_order_ledger``) books
        ``DR 5100 committee supplies expense / CR 1300 inventory — supplies on
        hand``. Crediting 1300 is an assertion that stock left the shelf, so an
        ad-hoc line entered but never marked used — real job cost, and counted
        by :attr:`actual_material_cost` — must not reach it, or the books write
        down inventory that was never issued.
        """
        return sum(
            (
                usage.actual_cost
                for usage in self.material_usage.all()
                if usage.was_used and usage.actual_cost is not None
            ),
            Decimal("0.00"),
        )

    @property
    def is_overdue(self) -> bool:
        """Return True if this open work order is past its due date."""
        from django.utils import timezone

        if self.status == self.Status.COMPLETED:
            return False
        if not self.due_date:
            return False
        return timezone.now().date() > self.due_date

    @staticmethod
    def short_id_for(work_order_id) -> str:
        """Short human-readable identifier built from a raw primary key.

        The one place the ``WO-XXXXXXXX`` format lives, so a caller holding
        only an id (a grouped-aggregate row, say) labels a work order exactly
        the way a loaded instance does.
        """
        return f"WO-{str(work_order_id)[:8].upper()}"

    @property
    def short_id(self) -> str:
        """Return a short human-readable identifier for this work order."""
        return WorkOrder.short_id_for(self.id)

    #: ``started_at`` rides along because ``start_timer`` stamps it.
    TIMER_FIELDS = ElapsedTimerModel.TIMER_FIELDS + ("started_at",)

    def start_timer(self, *, now=None) -> bool:
        """Start the clock, stamping ``started_at`` the very first time.

        ``started_at`` is *when work began*, not when the current segment
        began — a resume after lunch must not move it.
        """
        now = now or timezone.now()
        started = super().start_timer(now=now)
        if started and self.started_at is None:
            self.started_at = now
        return started


class WorkOrderTaskCompletion(ElapsedTimerModel):
    """
    Tracks completion of an individual task step within a work order.

    Created for each MaintenanceTask when a WorkOrder is generated,
    allowing granular tracking of which steps have been completed.

    Also carries its own stopwatch (:class:`ElapsedTimerModel`). Only one step
    per work order may run at a time — starting one pauses whichever other step
    was running — so the per-step totals partition the work rather than
    overlapping.
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

    Two kinds of row live here:

    * **Template-derived** — a frozen copy of the PM template's
      :class:`MaintenanceMaterial`, created up front at work-order generation.
      ``material`` points at the spec (nullable only because the spec may be
      deleted afterwards) and ``is_ad_hoc`` is False. These rows are part of
      the printed sheet, so they are never deletable.
    * **Ad-hoc** (op-768w) — added *during* the job through ``add_material``.
      No template spec exists (``material`` is null, ``is_ad_hoc`` True), which
      is the only kind of material a *corrective* work order can ever have: it
      has no PM template to copy from. An out-of-pocket buy is an ad-hoc row
      with a ``unit_cost`` and a ``receipt_image`` and no inventory link.

    ``unit_cost`` × ``quantity_used`` is the line's :attr:`actual_cost` — the
    real money spent, as opposed to the template's *estimated*
    ``MaintenanceMaterial.estimated_cost_per_unit``.
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
        help_text=(
            "The material specification (null if deleted after WO creation, or "
            "if this is an ad-hoc line typed in during the job)"
        ),
    )
    # Ad-hoc lines carry their own stock link because they have no
    # ``material`` spec to hang one off. Read :attr:`stock_item`, never either
    # field directly, so both kinds of row decrement the same way.
    inventory_item = models.ForeignKey(
        "InventoryItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_order_material_usage",
        help_text=(
            "Direct stock link for an ad-hoc line. Set it to draw the material "
            "from tracked inventory when the line is marked used; leave it null "
            "for an out-of-pocket buy, which moves no stock."
        ),
    )
    is_ad_hoc = models.BooleanField(
        default=False,
        help_text=(
            "True when this line was added during the job rather than copied "
            "from the PM template. Only ad-hoc lines can be removed."
        ),
    )
    # Provenance AND idempotency key for the PO bridge (op-bu80): a line that
    # was *ordered* for this work order gets exactly one usage row per purchase
    # order line, found-or-created on this FK. Null for every hand-entered row.
    purchase_order_item = models.ForeignKey(
        "reorder_queue.PurchaseOrderItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_order_material_usage",
        help_text=(
            "Purchase-order line this material was received from (null if it "
            "was entered by hand, or if the PO line was deleted afterwards)."
        ),
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
    # --- Actual cost capture (op-768w) --------------------------------------
    unit_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Actual price per unit paid.",
    )
    receipt_image = models.ImageField(
        upload_to="work_orders/receipts/%Y/%m/",
        null=True,
        blank=True,
        help_text="Photo of the receipt backing an out-of-pocket purchase.",
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

    @property
    def stock_item(self):
        """The inventory item this line draws from, or ``None`` (flag-only).

        An ad-hoc line links stock directly (:attr:`inventory_item`); a
        template-derived line inherits the link from its
        :class:`MaintenanceMaterial` spec. The direct link wins when both are
        set. This is the one accessor
        :func:`inventory.services.work_order_material_usage.apply_material_usage`
        consults, so both kinds of row decrement identically.
        """
        if self.inventory_item is not None:
            return self.inventory_item
        material = self.material
        return material.inventory_item if material is not None else None

    @property
    def actual_cost(self) -> Optional[Decimal]:
        """Real money spent on this line — ``quantity_used × unit_cost``.

        ``None`` when no ``unit_cost`` was recorded (cost is optional: plenty
        of lines are shop stock nobody prices at the point of use). Downstream
        cost reporting and the ledger charge sum this over the *used* lines —
        see :attr:`WorkOrder.actual_material_cost`.
        """
        if self.unit_cost is None:
            return None
        return (self.quantity_used or Decimal("0")) * self.unit_cost


class WorkOrderLotoCompletion(models.Model):
    """
    Tracks lockout/tagout (LOTO) of one energy source within a work order.

    Mirrors :class:`WorkOrderTaskCompletion`: one row per
    ``loto.AssetEnergySource`` on the WO's asset, created when a WorkOrder is
    generated so a scanned-back paper form has rows to apply marks against. The
    descriptive fields are denormalized at creation time (preserved even if the
    energy source is later edited or deleted) so the printed sheet and the
    persisted record stay in agreement — the ``loto_<id>`` OMR checkbox is keyed
    on this row's ``id``.

    This is the STRUCTURED half of "LOTO = both": the free-text half is
    ``WorkOrder.lockout_instructions`` (printed as a reference paragraph) plus
    ``WorkOrder.loto_completion_note`` (recorded web-side).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_order = models.ForeignKey(
        "WorkOrder",
        on_delete=models.CASCADE,
        related_name="loto_completions",
        help_text="The work order this LOTO completion belongs to",
    )
    energy_source = models.ForeignKey(
        "loto.AssetEnergySource",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_order_completions",
        help_text="The energy source (null if it was deleted after WO creation)",
    )
    source_type = models.CharField(
        max_length=20,
        blank=True,
        help_text="Denormalized energy-source type code (preserved if source deleted)",
    )
    source_label = models.CharField(
        max_length=200,
        help_text="Denormalized human label, e.g. 'Electrical (240V)'",
    )
    isolation_point = models.CharField(
        max_length=200,
        blank=True,
        help_text="Denormalized isolation point (where to lock out)",
    )
    required_devices = models.CharField(
        max_length=300,
        blank=True,
        help_text="Denormalized comma-joined list of required lockout devices",
    )
    is_completed = models.BooleanField(
        default=False,
        help_text="Whether this energy source has been isolated / locked out",
    )
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="completed_work_order_loto",
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["source_type", "source_label"]
        indexes = [
            models.Index(fields=["work_order", "is_completed"], name="wolc_wo_completed_idx"),
        ]

    def __str__(self) -> str:
        status = "✓" if self.is_completed else "○"
        return f"{status} {self.source_label} (WO {self.work_order.short_id})"


class WorkOrderPhoto(models.Model):
    """
    A photo attached to a work order by a technician.

    Used for documenting wear, damage, or completed work.

    A photo can optionally be pinned to a single step (``task_completion``) —
    the *evidence* half of the per-step photo pair: "here is what I did". Photos
    left unpinned (``task_completion=NULL``) are work-order-level, which is what
    every photo taken before per-step evidence existed is. Evidence photos are
    electronic only: they are captured after the sheet is printed, so they never
    appear on the PDF (only the step's reference image does).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_order = models.ForeignKey(
        "WorkOrder",
        on_delete=models.CASCADE,
        related_name="photos",
        help_text="The work order this photo belongs to",
    )
    task_completion = models.ForeignKey(
        "WorkOrderTaskCompletion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="evidence_photos",
        help_text=(
            "The step this photo documents. Null for work-order-level photos "
            "(and for photos whose step row was deleted)."
        ),
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


class WorkOrderAttachment(models.Model):
    """
    An arbitrary file attached to a standard (internal) work order.

    The generic attachments list the internal work order was missing: before
    this, a WO could only carry per-step evidence photos
    (:class:`WorkOrderPhoto`) and a scanned copy of its own paper form
    (:class:`WorkOrderSubmission`). Anything else a tech wanted to keep with
    the job — a supplier receipt, a datasheet page, a torque spec, a photo of
    the nameplate — had nowhere to live.

    Deliberately the same shape as the sibling lists on the other two order
    types, :class:`~maintenance_orders.models.ThirdPartyWorkOrderAttachment`
    and :class:`~reorder_queue.models.PurchaseOrderAttachment`, so the ScanTTY
    and web attachment screens can be written once. ``kind`` is the internal
    subset of the third-party choices: an internal WO has no invoice, FSR, or
    vendor quote.
    """

    class Kind(models.TextChoices):
        PHOTO = "photo", "Photo"
        DOCUMENT = "document", "Document"
        OTHER = "other", "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_order = models.ForeignKey(
        "WorkOrder",
        on_delete=models.CASCADE,
        related_name="attachments",
        help_text="The work order this attachment belongs to",
    )
    file = models.FileField(
        upload_to="work_orders/attachments/%Y/%m/",
        help_text="Attached file — photo, document, receipt, or anything else",
    )
    kind = models.CharField(
        max_length=32,
        choices=Kind.choices,
        default=Kind.OTHER,
        help_text="Rough category, so the attachments list can be grouped",
    )
    description = models.CharField(max_length=500, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_work_order_attachments",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]
        indexes = [
            models.Index(fields=["work_order", "kind"], name="woa_wo_kind_idx"),
            models.Index(fields=["-uploaded_at"], name="woa_uploaded_idx"),
        ]

    def __str__(self) -> str:
        label = self.description or self.file.name
        return f"{self.get_kind_display()} for WO {self.work_order.short_id}: {label}"


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
