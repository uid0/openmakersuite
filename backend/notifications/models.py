"""
Models for notification management.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import models

User = get_user_model()


class Notification(models.Model):
    """
    User notification model for storing persistent notifications.

    Notifications can be created for various events and will be displayed
    in the user's notification center.
    """

    TYPE_CHOICES = [
        ("success", "Success"),
        ("error", "Error"),
        ("warning", "Warning"),
        ("info", "Info"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="notifications",
        help_text="User who will receive this notification",
    )
    type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default="info",
        help_text="Type of notification",
    )
    title = models.CharField(
        max_length=200,
        help_text="Notification title",
    )
    message = models.TextField(
        help_text="Notification message",
    )
    read = models.BooleanField(
        default=False,
        help_text="Whether the notification has been read",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When the notification was created",
    )
    action_url = models.CharField(
        max_length=500,
        blank=True,
        null=True,
        help_text="Optional URL to navigate to when notification is clicked",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional metadata for the notification",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "read"]),
            models.Index(fields=["user", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.type}: {self.title} for {self.user.username}"

    def mark_as_read(self):
        """Mark this notification as read."""
        self.read = True
        self.save(update_fields=["read"])


class NotificationPreference(models.Model):
    """
    User notification preferences model.

    Stores user preferences for different types of notifications.
    Auto-created on first access with default values.
    """

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="notification_preferences",
        help_text="User who owns these preferences",
    )
    email_enabled = models.BooleanField(
        default=True,
        help_text="Enable email notifications",
    )
    in_app_enabled = models.BooleanField(
        default=True,
        help_text="Enable in-app notifications",
    )
    supply_alerts = models.BooleanField(
        default=True,
        help_text="Receive alerts about low supplies",
    )
    maintenance_alerts = models.BooleanField(
        default=True,
        help_text="Receive alerts about maintenance needs",
    )
    order_updates = models.BooleanField(
        default=True,
        help_text="Receive updates about order status",
    )
    system_notifications = models.BooleanField(
        default=True,
        help_text="Receive system notifications",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When the preferences were created",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="When the preferences were last updated",
    )

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"Notification preferences for {self.user.username}"
