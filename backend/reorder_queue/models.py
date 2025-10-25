"""
Models for reorder queue management.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from django.contrib.auth.models import User
from django.db import models

from inventory.models import InventoryItem


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
        from django.utils import timezone

        if self.status == self.PENDING:
            return (timezone.now() - self.requested_at).days
        return 0
