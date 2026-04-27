"""
Models for the interactive screens / kiosk display system.

Each `Screen` represents a physical display endpoint (Chromecast, Raspberry Pi
in kiosk mode, etc.). A screen renders a set of `ScreenContentBlock`s managed
by a Committee/SIG leader, plus any currently active `SystemMessage`s which
cannot be disabled by screen owners.

Screens post `ScreenHeartbeat` records so operators can see which endpoints are
alive and when the displayed content was last refreshed.
"""

import secrets
import uuid

from django.conf import settings
from django.contrib.auth.models import Group
from django.db import models
from django.utils import timezone


def generate_screen_token():
    """Generate a URL-safe token for public kiosk access."""
    return secrets.token_urlsafe(24)


class Screen(models.Model):
    """
    A physical display endpoint managed by OMS.

    Screens are identified by a slug (used in the public URL) and owned by a
    SIG/Committee (a Django Group). Committee/SIG leaders may customize the
    content blocks displayed on the screen.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(
        max_length=80,
        unique=True,
        help_text="URL-safe identifier used in the kiosk display URL (e.g. 'electronics-lab')",
    )
    name = models.CharField(max_length=200, help_text="Human-readable screen name")
    description = models.TextField(
        blank=True, help_text="Where this screen is located / its purpose"
    )
    location = models.ForeignKey(
        "inventory.Location",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="screens",
        help_text="Physical location of the screen (optional)",
    )
    sig = models.ForeignKey(
        Group,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="screens",
        help_text="Owning SIG/Committee — its admins may customize content blocks",
    )
    is_active = models.BooleanField(
        default=True, help_text="Whether this screen is currently in service"
    )
    rotation_interval_seconds = models.PositiveIntegerField(
        default=15,
        help_text="Seconds between slide rotations on the kiosk",
    )
    refresh_interval_seconds = models.PositiveIntegerField(
        default=60,
        help_text="Seconds between content refreshes from the server",
    )
    access_token = models.CharField(
        max_length=64,
        unique=True,
        default=generate_screen_token,
        help_text="Secret token included in the public kiosk URL",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_screens",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["is_active"]),
            models.Index(fields=["sig"]),
        ]

    def __str__(self):
        return self.name

    @property
    def last_heartbeat(self):
        """Most recent heartbeat, or None."""
        return self.heartbeats.order_by("-reported_at").first()

    @property
    def is_online(self):
        """Considered online if a heartbeat was received in the last 5 minutes."""
        hb = self.last_heartbeat
        if hb is None:
            return False
        return (timezone.now() - hb.reported_at).total_seconds() <= 300


class SystemMessage(models.Model):
    """
    A system-wide important message that appears on every active screen.

    Only superusers / staff can manage these. Screen owners cannot disable
    them — they are always rendered while `is_active` is True and within the
    active window (`starts_at`/`ends_at`).
    """

    LEVEL_INFO = "info"
    LEVEL_WARNING = "warning"
    LEVEL_CRITICAL = "critical"
    LEVEL_CHOICES = [
        (LEVEL_INFO, "Info"),
        (LEVEL_WARNING, "Warning"),
        (LEVEL_CRITICAL, "Critical"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=200)
    body = models.TextField(help_text="Message body — shown on every active screen")
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default=LEVEL_INFO)
    is_active = models.BooleanField(default=True)
    starts_at = models.DateTimeField(
        null=True, blank=True, help_text="Show from this time (optional)"
    )
    ends_at = models.DateTimeField(
        null=True, blank=True, help_text="Hide after this time (optional)"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_system_messages",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["is_active", "starts_at", "ends_at"]),
        ]

    def __str__(self):
        return f"[{self.get_level_display()}] {self.title}"

    def is_currently_visible(self, now=None):
        now = now or timezone.now()
        if not self.is_active:
            return False
        if self.starts_at and now < self.starts_at:
            return False
        if self.ends_at and now > self.ends_at:
            return False
        return True

    @classmethod
    def active_for_now(cls, now=None):
        """Return a queryset of messages active at `now` (default: now)."""
        now = now or timezone.now()
        qs = cls.objects.filter(is_active=True)
        qs = qs.filter(models.Q(starts_at__isnull=True) | models.Q(starts_at__lte=now))
        qs = qs.filter(models.Q(ends_at__isnull=True) | models.Q(ends_at__gte=now))
        return qs


class ScreenContentBlock(models.Model):
    """
    A content block on a screen, managed by Committee/SIG leaders.

    Committee/SIG leaders can choose to display tool status, asset usage,
    custom messages, weather, traffic, or skip this slot entirely (by marking
    `is_enabled=False`).
    """

    BLOCK_TOOL_STATUS = "tool_status"
    BLOCK_ASSET_USAGE = "asset_usage"
    BLOCK_CUSTOM_TEXT = "custom_text"
    BLOCK_ANNOUNCEMENT = "announcement"
    BLOCK_WEATHER = "weather"
    BLOCK_SHARED_WEATHER = "shared_weather"
    BLOCK_TRAFFIC = "traffic"
    BLOCK_SHARED_TRAFFIC = "shared_traffic"
    BLOCK_MEMBER_COUNT = "member_count"
    BLOCK_BOARD_MEETING = "board_meeting"

    BLOCK_TYPE_CHOICES = [
        (BLOCK_TOOL_STATUS, "Tool Status"),
        (BLOCK_ASSET_USAGE, "Asset Usage"),
        (BLOCK_CUSTOM_TEXT, "Custom Text / Rules Reminder"),
        (BLOCK_ANNOUNCEMENT, "Announcement / Class Slide"),
        (BLOCK_WEATHER, "Weather"),
        (BLOCK_SHARED_WEATHER, "Shared Weather"),
        (BLOCK_TRAFFIC, "Traffic"),
        (BLOCK_SHARED_TRAFFIC, "Shared Traffic (unified site map)"),
        (BLOCK_MEMBER_COUNT, "Member Count"),
        (BLOCK_BOARD_MEETING, "Next Board Meeting"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    screen = models.ForeignKey(Screen, on_delete=models.CASCADE, related_name="content_blocks")
    block_type = models.CharField(max_length=40, choices=BLOCK_TYPE_CHOICES)
    title = models.CharField(
        max_length=200, blank=True, help_text="Optional heading shown above the block"
    )
    body = models.TextField(
        blank=True, help_text="Free-form body (used by custom_text / announcement blocks)"
    )
    config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Block-specific configuration (e.g. location filter, asset ids)",
    )
    order = models.PositiveIntegerField(default=0, help_text="Display order (ascending)")
    is_enabled = models.BooleanField(
        default=True, help_text="Committee leaders can hide individual blocks"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["screen", "order", "created_at"]
        indexes = [
            models.Index(fields=["screen", "is_enabled"]),
        ]

    def __str__(self):
        return f"{self.screen.name} — {self.get_block_type_display()}"


class ScreenHeartbeat(models.Model):
    """
    A heartbeat posted by a kiosk endpoint so operators can see it's alive.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    screen = models.ForeignKey(Screen, on_delete=models.CASCADE, related_name="heartbeats")
    reported_at = models.DateTimeField(default=timezone.now)
    user_agent = models.CharField(max_length=255, blank=True)
    client_ip = models.GenericIPAddressField(null=True, blank=True)
    content_version = models.CharField(
        max_length=64,
        blank=True,
        help_text="Hash/version of the content the kiosk is currently showing",
    )

    class Meta:
        ordering = ["-reported_at"]
        indexes = [
            models.Index(fields=["screen", "-reported_at"]),
        ]

    def __str__(self):
        return f"{self.screen.name} @ {self.reported_at.isoformat()}"
