"""
Models for notification management.
"""

from __future__ import annotations

import uuid

from django.contrib.auth import get_user_model
from django.core.validators import MaxValueValidator, MinValueValidator
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
    recent_pages_limit = models.PositiveIntegerField(
        default=8,
        validators=[MinValueValidator(1), MaxValueValidator(50)],
        help_text="Maximum number of recent pages to display in sidebar (1-50)",
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


class KnownDevice(models.Model):
    """
    A device/browser previously seen signing in to a user's account.

    Used to detect *new* device sign-ins for staff/privileged accounts so an
    informational security alert can be raised (oms-bwcdb, FP1 of the
    new-device-login-alert series). A device is identified by an opaque,
    server-minted ``device_token`` persisted in a Django-signed, httpOnly
    cookie on the client; see ``notifications.device_login``.

    App placement: this lives in ``notifications`` rather than a new ``devices``
    app because the ``devices`` app already models physical network interfaces
    (``inventory.Asset`` interfaces / network drops) — an unrelated domain. The
    login-device alert is tightly coupled to the notification it raises, so the
    bead sanctioned the notifications app as the home. A later phase
    (FP3 revoke / FP4 device UI) may relocate this to a dedicated
    account-security app if preferred.
    """

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="known_devices",
        help_text="User this device is known for",
    )
    device_token = models.CharField(
        max_length=128,
        db_index=True,
        help_text="Opaque server-minted device id (stored client-side in a signed cookie)",
    )
    fingerprint_hash = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        help_text="Hash of the client JS fingerprint; populated in FP4, null until then",
    )
    user_agent = models.TextField(
        blank=True,
        help_text="User-Agent string captured for this device",
    )
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text="Approximate IP address last seen for this device",
    )
    label = models.CharField(
        max_length=120,
        blank=True,
        help_text="Optional user-friendly label for this device",
    )
    first_seen = models.DateTimeField(
        auto_now_add=True,
        help_text="When this device was first seen",
    )
    last_seen = models.DateTimeField(
        auto_now=True,
        help_text="When this device was most recently seen",
    )

    class Meta:
        ordering = ["-last_seen"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "device_token"],
                name="notifications_knowndevice_unique_user_token",
            ),
        ]

    def __str__(self):
        token_preview = (self.device_token or "")[:8]
        return f"Known device for {self.user.username} ({token_preview})"


class AccountSecurityAuditEvent(models.Model):
    """
    Append-only audit trail for account-security actions a user takes on their
    own devices/sessions (notifications FP3, oms-ltqs3).

    Two actions are recorded:

    * ``revoke_all`` — the "this wasn't me" action: every Django session for the
      user is deleted and ``User.tokens_valid_after`` is advanced so all
      outstanding JWTs are rejected, forcing a full re-auth everywhere.
    * ``forget_device`` — a single :class:`KnownDevice` row was removed.

    ``actor`` is the account owner performing the action; it is ``SET_NULL`` so
    the audit row survives user deletion. Mirrors the per-domain audit-event
    pattern used elsewhere (e.g. ``customization.SiteSettingsAuditEvent``).
    """

    ACTION_REVOKE_ALL = "revoke_all"
    ACTION_FORGET_DEVICE = "forget_device"
    ACTION_CHOICES = [
        (ACTION_REVOKE_ALL, "Revoked all sessions and tokens"),
        (ACTION_FORGET_DEVICE, "Forgot a known device"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="account_security_events",
        help_text="User who performed the action on their own account.",
    )
    action = models.CharField(max_length=32, choices=ACTION_CHOICES)
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text="Approximate IP address the action originated from.",
    )
    user_agent = models.TextField(
        blank=True,
        help_text="User-Agent string of the request that performed the action.",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Structured detail, e.g. sessions_deleted count or device id.",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        actor = self.actor.username if self.actor else "(deleted user)"
        return f"{self.get_action_display()} by {actor}"
