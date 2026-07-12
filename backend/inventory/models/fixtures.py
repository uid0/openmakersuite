"""Fixture and fixture refill-request models."""

from __future__ import annotations

import uuid
from typing import Optional

from django.db import models


class Fixture(models.Model):
    """
    Fixed assets that require periodic refilling (e.g., soap dispensers, paper towel holders).

    Fixtures are installed at specific locations and use inventory items as refills.
    Users can scan QR codes to request refills.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(
        max_length=200,
        help_text="Descriptive name for this fixture (e.g., 'Bathroom 1 Soap Dispenser')",
    )
    description = models.TextField(blank=True, help_text="Additional details about this fixture")
    location = models.ForeignKey(
        "Location",
        on_delete=models.PROTECT,
        related_name="fixtures",
        help_text="Where this fixture is installed",
    )
    refill_item = models.ForeignKey(
        "InventoryItem",
        on_delete=models.PROTECT,
        related_name="used_in_fixtures",
        help_text="The inventory item used to refill this fixture",
    )
    asset_tag = models.CharField(
        max_length=100,
        blank=True,
        unique=True,
        null=True,
        help_text="Unique asset tag or identifier",
    )
    is_active = models.BooleanField(
        default=True, help_text="Inactive fixtures are hidden from scanning"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["location__name", "name"]
        indexes = [
            models.Index(fields=["location", "is_active"]),
            models.Index(fields=["refill_item"]),
            models.Index(fields=["asset_tag"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.location.name})"

    @property
    def pending_requests_count(self) -> int:
        """Count of pending refill requests for this fixture."""
        return self.refill_requests.filter(status=FixtureRefillRequest.Status.PENDING).count()


class FixtureRefillRequest(models.Model):
    """
    A request to refill a fixture.

    Created when someone scans a fixture's QR code to report it needs refilling.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fixture = models.ForeignKey(
        "Fixture",
        on_delete=models.CASCADE,
        related_name="refill_requests",
        help_text="The fixture that needs refilling",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        help_text="Current status of this refill request",
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    requested_by = models.CharField(
        max_length=200,
        blank=True,
        help_text="Username or identifier of person who reported this (optional)",
    )
    resolved_at = models.DateTimeField(
        null=True, blank=True, help_text="When this request was resolved"
    )
    resolved_by = models.CharField(
        max_length=200,
        blank=True,
        help_text="Username of person who resolved this request",
    )
    notes = models.TextField(blank=True, help_text="Additional notes about this request")

    class Meta:
        ordering = ["-requested_at"]
        indexes = [
            models.Index(fields=["fixture", "status"]),
            models.Index(fields=["status", "-requested_at"]),
        ]

    def __str__(self) -> str:
        return f"Refill request for {self.fixture.name} ({self.get_status_display()})"

    @property
    def time_to_resolve(self) -> Optional[int]:
        """Calculate time in minutes from request to resolution."""
        if self.resolved_at:
            delta = self.resolved_at - self.requested_at
            return int(delta.total_seconds() / 60)
        return None
