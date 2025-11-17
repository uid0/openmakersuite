"""
Models for checklist system.
"""

import uuid

from django.conf import settings
from django.contrib.auth.models import Group
from django.db import models
from django.utils import timezone


class Checklist(models.Model):
    """
    Checklist model for SIG tasks and inspections.

    Checklists can be associated with assets, locations, or inventory items
    and can be completed by scanning QR codes.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, help_text="Checklist name, e.g., 'Monthly Scrum Walk'")
    description = models.TextField(
        blank=True, help_text="Description of what this checklist covers"
    )
    sig = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        related_name="checklists",
        help_text="SIG (Group) that owns this checklist",
    )
    is_active = models.BooleanField(
        default=True, help_text="Whether this checklist is currently available"
    )
    is_public = models.BooleanField(
        default=True, help_text="Whether unauthenticated users can access this checklist"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_checklists",
        help_text="User who created this checklist",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["sig", "is_active"]),
            models.Index(fields=["is_active", "is_public"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.sig.name})"


class ChecklistStep(models.Model):
    """
    Individual step in a checklist.

    Each step can be associated with an asset, location, or inventory item
    that needs to be scanned to complete the step.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    checklist = models.ForeignKey(Checklist, on_delete=models.CASCADE, related_name="steps")
    step_number = models.IntegerField(help_text="Order of this step in the checklist")
    name = models.CharField(max_length=200, help_text="Step name/description")
    asset = models.ForeignKey(
        "inventory.Asset",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="checklist_steps",
        help_text="Associated asset to scan for this step",
    )
    location = models.ForeignKey(
        "inventory.Location",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="checklist_steps",
        help_text="Associated location to scan for this step",
    )
    inventory_item = models.ForeignKey(
        "inventory.InventoryItem",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="checklist_steps",
        help_text="Associated inventory item to scan for this step",
    )
    required = models.BooleanField(default=True, help_text="Whether this step must be completed")
    notes = models.TextField(blank=True, help_text="Additional instructions for this step")

    class Meta:
        ordering = ["checklist", "step_number"]
        unique_together = [["checklist", "step_number"]]
        indexes = [
            models.Index(fields=["checklist", "step_number"]),
        ]

    def __str__(self):
        return f"{self.checklist.name} - Step {self.step_number}: {self.name}"

    def clean(self):
        """Validate that exactly one of asset, location, or inventory_item is set."""
        from django.core.exceptions import ValidationError

        count = sum(
            [
                bool(self.asset),
                bool(self.location),
                bool(self.inventory_item),
            ]
        )
        if count != 1:
            raise ValidationError("Exactly one of asset, location, or inventory_item must be set.")

    def save(self, *args, **kwargs):
        """Call clean before saving."""
        self.clean()
        super().save(*args, **kwargs)


class ChecklistCompletion(models.Model):
    """
    Record of a checklist completion attempt.

    Tracks who is completing the checklist, when they started,
    and when they finished.
    """

    STATUS_IN_PROGRESS = "in_progress"
    STATUS_COMPLETED = "completed"
    STATUS_ABANDONED = "abandoned"

    STATUS_CHOICES = [
        (STATUS_IN_PROGRESS, "In Progress"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_ABANDONED, "Abandoned"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    checklist = models.ForeignKey(Checklist, on_delete=models.CASCADE, related_name="completions")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="checklist_completions",
        help_text="Authenticated user completing the checklist",
    )
    user_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Name/handle for unauthenticated users",
    )
    started_at = models.DateTimeField(auto_now_add=True, help_text="When completion started")
    completed_at = models.DateTimeField(
        null=True, blank=True, help_text="When checklist was completed"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_IN_PROGRESS)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["checklist", "status"]),
            models.Index(fields=["user", "status"]),
            models.Index(fields=["status", "-started_at"]),
        ]

    def __str__(self):
        user_str = self.user.username if self.user else (self.user_name or "Anonymous")
        return f"{self.checklist.name} - {user_str} ({self.status})"

    def mark_completed(self):
        """Mark this completion as completed."""
        self.status = self.STATUS_COMPLETED
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "completed_at"])


class ChecklistStepCompletion(models.Model):
    """
    Record of completing a specific step in a checklist.

    Stores which asset/location/item was scanned, when it was scanned,
    and any notes from the user.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    completion = models.ForeignKey(
        ChecklistCompletion, on_delete=models.CASCADE, related_name="step_completions"
    )
    step = models.ForeignKey(ChecklistStep, on_delete=models.CASCADE, related_name="completions")
    scanned_at = models.DateTimeField(auto_now_add=True, help_text="When the QR code was scanned")
    scanned_asset = models.ForeignKey(
        "inventory.Asset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="checklist_step_completions",
        help_text="Which asset was scanned",
    )
    scanned_location = models.ForeignKey(
        "inventory.Location",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="checklist_step_completions",
        help_text="Which location was scanned",
    )
    scanned_item = models.ForeignKey(
        "inventory.InventoryItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="checklist_step_completions",
        help_text="Which inventory item was scanned",
    )
    notes = models.TextField(blank=True, help_text="User notes for this step")

    class Meta:
        ordering = ["completion", "step__step_number"]
        unique_together = [["completion", "step"]]
        indexes = [
            models.Index(fields=["completion", "step"]),
            models.Index(fields=["scanned_at"]),
        ]

    def __str__(self):
        return f"{self.completion.checklist.name} - Step {self.step.step_number} by {self.completion.user or self.completion.user_name or 'Anonymous'}"
