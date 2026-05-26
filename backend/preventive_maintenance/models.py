"""Preventive-maintenance schedules and service logs.

`PMSchedule` describes one recurring task pinned to an asset (e.g.
"Replace HEPA filter every 90 days"). `PMServiceLog` records each time
that task is performed. The board endpoint walks every active schedule
and surfaces the current status — `ok`, `warning`, `overdue`, or
`never` — so an Inkplate / dashboard can render days-since-service
counters without computing them client-side.

Why a separate app vs. extending `maintenance_orders`:

`maintenance_orders.ThirdPartyWorkOrder` is the heavy workflow for
externally-sourced repairs — quotes, audit logs, attachments,
emergency authorizations. PM tasks are lightweight repeats ("change
the filter") with no procurement story, so they live alongside rather
than inside the WO machinery.
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class PMSchedule(models.Model):
    """A recurring preventive-maintenance task for an asset."""

    STATUS_NEVER = "never"
    STATUS_OK = "ok"
    STATUS_WARNING = "warning"
    STATUS_OVERDUE = "overdue"

    # Fraction of interval_days at which `ok` becomes `warning`. 0.8 →
    # a 90-day schedule flips to warning at day 72 (18 days remaining).
    # Inkplates display warnings in inverted ink; overdue in solid black.
    WARNING_RATIO = 0.8

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(
        "inventory.Asset",
        on_delete=models.CASCADE,
        related_name="pm_schedules",
        help_text="Which asset this task is performed on.",
    )
    task_name = models.CharField(
        max_length=200,
        help_text='Short human label, e.g. "Replace HEPA filter".',
    )
    description = models.TextField(
        blank=True,
        help_text="Optional longer instructions or part numbers.",
    )
    interval_days = models.PositiveIntegerField(
        help_text="How often the task should be performed, in days.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive schedules are hidden from the PM board.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["asset__name", "task_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["asset", "task_name"],
                name="unique_pm_schedule_per_asset_task",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.task_name} — {self.asset.name}"

    def last_log(self) -> "PMServiceLog | None":
        return self.service_logs.order_by("-performed_at").first()

    def days_since_last(self) -> int | None:
        log = self.last_log()
        if log is None:
            return None
        return (timezone.now().date() - log.performed_at.date()).days

    def days_until_due(self) -> int | None:
        days = self.days_since_last()
        if days is None:
            return None
        return self.interval_days - days

    def status(self) -> str:
        days = self.days_since_last()
        if days is None:
            return self.STATUS_NEVER
        if days >= self.interval_days:
            return self.STATUS_OVERDUE
        if days >= int(self.interval_days * self.WARNING_RATIO):
            return self.STATUS_WARNING
        return self.STATUS_OK


class PMServiceLog(models.Model):
    """One performed PM service event."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    schedule = models.ForeignKey(
        PMSchedule,
        on_delete=models.CASCADE,
        related_name="service_logs",
        help_text="The recurring task this log entry satisfies.",
    )
    performed_at = models.DateTimeField(
        default=timezone.now,
        help_text="When the service was performed. Defaults to now.",
    )
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pm_service_logs",
        help_text="Who performed the service. Null for anonymous kiosk entries.",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-performed_at"]

    def __str__(self) -> str:
        when = self.performed_at.date().isoformat()
        return f"{self.schedule.task_name} @ {when}"
