"""
Location Check-in and Feedback Models

Handles location-specific QR code functionality including:
- Volunteer/contractor check-ins for safety, cleanliness, and security checks
- Anonymous user feedback
- Security concern reporting
- Task management for negative feedback
"""

import uuid

from django.db import models
from django.utils import timezone

from inventory.models import Location


class LocationCheckIn(models.Model):
    """
    Check-in record for volunteers, contractors, or anonymous users at a location.
    Used for safety, cleanliness, and security tracking.
    """

    CHECKIN_TYPE_CHOICES = [
        ("volunteer", "Volunteer"),
        ("contractor", "Contractor"),
        ("anonymous", "Anonymous User"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name="check_ins",
        help_text="Location where check-in occurred",
    )
    checkin_type = models.CharField(
        max_length=20,
        choices=CHECKIN_TYPE_CHOICES,
        default="anonymous",
        help_text="Type of user checking in",
    )
    user = models.ForeignKey(
        "membership.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="location_check_ins",
        help_text="Authenticated user (null for anonymous)",
    )
    checked_in_at = models.DateTimeField(auto_now_add=True, help_text="When the check-in occurred")
    notes = models.TextField(blank=True, help_text="Optional notes about the check-in")

    class Meta:
        ordering = ["-checked_in_at"]
        indexes = [
            models.Index(fields=["location", "-checked_in_at"]),
            models.Index(fields=["checkin_type", "-checked_in_at"]),
        ]

    def __str__(self):
        user_str = self.user.username if self.user else "Anonymous"
        return f"{self.checkin_type.title()} check-in at {self.location.name} by {user_str}"


class LocationFeedback(models.Model):
    """
    Anonymous or authenticated feedback about a location.
    Negative feedback automatically creates tasks.
    """

    FEEDBACK_TYPE_CHOICES = [
        ("positive", "Positive"),
        ("neutral", "Neutral"),
        ("negative", "Negative"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name="feedback",
        help_text="Location the feedback is about",
    )
    feedback_type = models.CharField(
        max_length=20, choices=FEEDBACK_TYPE_CHOICES, help_text="Type of feedback"
    )
    message = models.TextField(help_text="Feedback message")
    user = models.ForeignKey(
        "membership.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="location_feedback",
        help_text="Authenticated user (null for anonymous)",
    )
    submitted_at = models.DateTimeField(
        auto_now_add=True, help_text="When the feedback was submitted"
    )
    is_resolved = models.BooleanField(
        default=False, help_text="Whether this feedback has been resolved"
    )

    class Meta:
        ordering = ["-submitted_at"]
        indexes = [
            models.Index(fields=["location", "-submitted_at"]),
            models.Index(fields=["feedback_type", "-submitted_at"]),
            models.Index(fields=["is_resolved"]),
        ]

    def __str__(self):
        user_str = self.user.username if self.user else "Anonymous"
        return f"{self.feedback_type} feedback for {self.location.name} by {user_str}"


class SecurityReport(models.Model):
    """
    Safety and cleaning concern reports from anonymous or authenticated users.
    """

    REPORT_TYPE_CHOICES = [
        ("cleaning", "Needs Cleaning"),
        ("safety", "Safety Concern"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name="security_reports",
        help_text="Location where the concern was observed",
    )
    report_type = models.CharField(
        max_length=20,
        choices=REPORT_TYPE_CHOICES,
        default="safety",
        help_text="Type of report: cleaning or safety concern",
    )
    is_urgent = models.BooleanField(
        default=False, help_text="Whether this needs immediate attention"
    )
    description = models.TextField(blank=True, help_text="Optional additional details")
    user = models.ForeignKey(
        "membership.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="security_reports",
        help_text="Authenticated user (null for anonymous)",
    )
    reported_at = models.DateTimeField(auto_now_add=True, help_text="When the report was submitted")
    is_resolved = models.BooleanField(
        default=False, help_text="Whether this security report has been resolved"
    )
    resolved_at = models.DateTimeField(
        null=True, blank=True, help_text="When the security report was resolved"
    )
    resolved_by = models.ForeignKey(
        "membership.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_security_reports",
        help_text="User who resolved this report",
    )

    class Meta:
        ordering = ["-reported_at"]
        indexes = [
            models.Index(fields=["location", "-reported_at"]),
            models.Index(fields=["report_type", "-reported_at"]),
            models.Index(fields=["is_urgent", "-reported_at"]),
            models.Index(fields=["is_resolved"]),
        ]

    def __str__(self):
        user_str = self.user.username if self.user else "Anonymous"
        report_type_display = dict(self.REPORT_TYPE_CHOICES).get(self.report_type, self.report_type)
        return f"{report_type_display} for {self.location.name} by {user_str}"


class LocationTask(models.Model):
    """
    Tasks created from negative feedback or security reports.
    Volunteers can complete these tasks.
    """

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("in_progress", "In Progress"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name="tasks",
        help_text="Location where the task needs to be completed",
    )
    title = models.CharField(max_length=200, help_text="Task title")
    description = models.TextField(help_text="Task description")
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
        help_text="Current status of the task",
    )
    created_from_feedback = models.ForeignKey(
        LocationFeedback,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_tasks",
        help_text="Feedback that created this task (if applicable)",
    )
    created_from_security_report = models.ForeignKey(
        SecurityReport,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_tasks",
        help_text="Security report that created this task (if applicable)",
    )
    created_by = models.ForeignKey(
        "membership.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_location_tasks",
        help_text="User who created this task (system if auto-created)",
    )
    assigned_to = models.ForeignKey(
        "membership.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_location_tasks",
        help_text="User assigned to complete this task",
    )
    completed_by = models.ForeignKey(
        "membership.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="completed_location_tasks",
        help_text="User who completed this task",
    )
    created_at = models.DateTimeField(auto_now_add=True, help_text="When the task was created")
    completed_at = models.DateTimeField(
        null=True, blank=True, help_text="When the task was completed"
    )
    due_date = models.DateTimeField(
        null=True, blank=True, help_text="Optional due date for the task"
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["location", "status"]),
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["assigned_to", "status"]),
        ]

    def __str__(self):
        return f"{self.title} at {self.location.name} ({self.status})"

    def mark_completed(self, user):
        """Mark task as completed by a user."""
        self.status = "completed"
        self.completed_by = user
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "completed_by", "completed_at"])
