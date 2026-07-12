"""Location problem reports."""

from __future__ import annotations

import uuid

from django.db import models


class LocationProblem(models.Model):
    """
    Track problems reported against a Location (not a specific asset).

    Locations can host non-asset issues — leaks, broken doors, lighting,
    HVAC complaints — that have no home in AssetProblem. A LocationProblem
    is a report; promote it to a WorkOrder (PM) or ThirdPartyWorkOrder
    when work is scheduled.
    """

    class Status(models.TextChoices):
        REPORTED = "reported", "Reported"
        IN_PROGRESS = "in_progress", "In Progress"
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"

    class Severity(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    location = models.ForeignKey(
        "Location",
        on_delete=models.CASCADE,
        related_name="problems",
        help_text="The location with the problem",
    )
    reported_by = models.CharField(
        max_length=200,
        blank=True,
        help_text="Username or identifier of person reporting the problem",
    )
    description = models.TextField(help_text="Description of the problem or issue")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.REPORTED,
    )
    severity = models.CharField(
        max_length=10,
        choices=Severity.choices,
        default=Severity.MEDIUM,
    )
    photo = models.ImageField(
        upload_to="location_problems/%Y/%m/",
        null=True,
        blank=True,
        help_text="Optional reporter photo of the problem",
    )
    paper_form_attachment = models.FileField(
        upload_to="location_problems/paper/%Y/%m/",
        null=True,
        blank=True,
        help_text="Scanned paper-form PDF if reported via paper",
    )
    work_order = models.ForeignKey(
        "WorkOrder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="location_problems",
        help_text="Standard PM work order this problem was promoted to",
    )
    third_party_work_order = models.ForeignKey(
        "maintenance_orders.ThirdPartyWorkOrder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="location_problems",
        help_text="Third-party work order this problem was promoted to",
    )
    resolution_notes = models.TextField(blank=True)
    reported_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-reported_at"]
        indexes = [
            models.Index(fields=["location", "status"]),
            models.Index(fields=["status", "reported_at"]),
            models.Index(fields=["severity", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.location.name} - {self.get_status_display()} " f"({self.reported_at.date()})"
