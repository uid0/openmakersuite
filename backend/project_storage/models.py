"""Models for the Project Storage system.

Members can park work-in-progress in a designated project storage area for
up to 30 days at a stretch. After the 30 days expire, the storage warden
sends a violation notice; if the member doesn't remove the items within
7 days, the items move to a "purgatory" location. After a stint is
removed, the member has to wait 3 days before starting a new one.

This module models a single *stint* (one occupancy by one member) plus an
append-only audit log of events on that stint.
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Optional

from django.conf import settings
from django.db import models
from django.utils import timezone

DEFAULT_STINT_DAYS = 30
DEFAULT_PURGATORY_GRACE_DAYS = 7
DEFAULT_REENTRY_COOLDOWN_DAYS = 3
EXPIRING_SOON_WINDOW_DAYS = 3


def _generate_stint_id() -> str:
    """Short, URL-safe identifier printed inside the QR code on the label.

    8 characters of a Crockford-base32-ish alphabet (no I/L/O/0/1) so a
    warden reading it off the label by eye isn't confused. ~10^11 keyspace
    is plenty for a makerspace's lifetime of stints.
    """
    alphabet = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
    return "PS-" + "".join(secrets.choice(alphabet) for _ in range(8))


class ProjectStorageStint(models.Model):
    """One occupancy of a project storage shelf/area by one member."""

    STATUS_ACTIVE = "active"
    STATUS_EXPIRING_SOON = "expiring_soon"
    STATUS_EXPIRED = "expired"
    STATUS_PURGATORY_WARNED = "purgatory_warned"
    STATUS_PURGATORY = "purgatory"
    STATUS_REMOVED = "removed"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_EXPIRING_SOON, "Expiring soon"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_PURGATORY_WARNED, "Purgatory warned"),
        (STATUS_PURGATORY, "Purgatory"),
        (STATUS_REMOVED, "Removed"),
    ]

    # Stable, short identifier — encoded in the QR + printed in eye-readable
    # form below the QR so a warden can call it out over the radio.
    stint_id = models.CharField(max_length=16, unique=True, default=_generate_stint_id)

    # Member identity. We keep first/last/email denormalized so the label
    # and the violation email stay readable even after the member's WHMCS
    # row changes (renames, email rotates, etc.).
    username = models.CharField(max_length=64, db_index=True)
    first_name = models.CharField(max_length=64, blank=True)
    last_name = models.CharField(max_length=64, blank=True)
    email = models.EmailField(blank=True)

    # Optional human-friendly tag for the project (e.g. "Restoring my
    # 1972 Schwinn") so the warden has more context than a member name.
    project_title = models.CharField(max_length=120, blank=True)

    started_at = models.DateTimeField(default=timezone.now)
    # expires_at is denormalized from started_at + 30 days so the warden
    # scan path is a single SELECT and so that overriding the window per
    # stint (e.g. a one-off 14-day stint for a known clear-out) doesn't
    # require config plumbing.
    expires_at = models.DateTimeField()

    # When the member or warden marks the stint cleared. Drives the
    # 3-day re-entry cooldown.
    removed_at = models.DateTimeField(null=True, blank=True)

    # When the warden sent the violation notice. Once set, the 7-day
    # purgatory_grace clock starts; purgatory_at is computed from this.
    notice_sent_at = models.DateTimeField(null=True, blank=True)

    # When the items physically moved to purgatory. The warden marks this
    # explicitly so we don't auto-purgatory on a clock and then have to
    # back it out when nobody actually moved the items.
    moved_to_purgatory_at = models.DateTimeField(null=True, blank=True)

    # Designated locations come from inventory.Location. We don't FK to
    # avoid creating a hard inventory dependency at migrate time, but the
    # admin should set these from the SiteSettings page once the spots
    # are created. Storing the location *name* keeps it cheap to read.
    storage_location_name = models.CharField(max_length=120, blank=True)
    purgatory_location_name = models.CharField(max_length=120, blank=True)

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["username", "-started_at"]),
            models.Index(fields=["expires_at"]),
            models.Index(fields=["removed_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.stint_id} · {self.username}"

    def save(self, *args, **kwargs):
        # Backfill expires_at from started_at so the API caller doesn't
        # have to compute it. If a future use case wants a custom window,
        # set expires_at explicitly before save().
        if not self.expires_at:
            self.expires_at = self.started_at + timedelta(days=DEFAULT_STINT_DAYS)
        super().save(*args, **kwargs)

    # ------------------------------------------------------------------
    # Computed status — single source of truth for the warden UI badges
    # ------------------------------------------------------------------

    def compute_status(self, now: Optional[timezone.datetime] = None) -> str:
        """Return one of STATUS_*.

        Order matters: terminal states (removed, purgatory) win, then
        warned → expired → expiring_soon → active.
        """
        now = now or timezone.now()
        if self.removed_at is not None:
            return self.STATUS_REMOVED
        if self.moved_to_purgatory_at is not None:
            return self.STATUS_PURGATORY
        if self.notice_sent_at is not None:
            return self.STATUS_PURGATORY_WARNED
        if now >= self.expires_at:
            return self.STATUS_EXPIRED
        if now >= self.expires_at - timedelta(days=EXPIRING_SOON_WINDOW_DAYS):
            return self.STATUS_EXPIRING_SOON
        return self.STATUS_ACTIVE

    @property
    def purgatory_at(self) -> Optional[timezone.datetime]:
        """When this stint *will* move to purgatory if not removed.

        Only meaningful once a notice has been sent.
        """
        if self.notice_sent_at is None:
            return None
        return self.notice_sent_at + timedelta(days=DEFAULT_PURGATORY_GRACE_DAYS)

    @property
    def display_name(self) -> str:
        name = " ".join(p for p in (self.first_name, self.last_name) if p).strip()
        return name or self.username

    @property
    def expiry_week_and_day(self) -> tuple[int, int]:
        """ISO-week and day-of-year of expires_at — printed in big type."""
        local = timezone.localtime(self.expires_at)
        return local.isocalendar().week, local.timetuple().tm_yday

    # ------------------------------------------------------------------
    # Validation helpers used by the API layer
    # ------------------------------------------------------------------

    @classmethod
    def cooldown_blocks_new_stint(
        cls, username: str, now: Optional[timezone.datetime] = None
    ) -> Optional[timezone.datetime]:
        """If the member can't start a new stint yet, return the unblock time.

        The 3-day cooldown begins when their most recent stint was marked
        removed. If they have no prior removed stint, or the last removal
        was more than 3 days ago, returns None (they're clear to start).
        """
        now = now or timezone.now()
        latest_removed = (
            cls.objects.filter(username=username, removed_at__isnull=False)
            .order_by("-removed_at")
            .first()
        )
        if latest_removed is None:
            return None
        unblock = latest_removed.removed_at + timedelta(days=DEFAULT_REENTRY_COOLDOWN_DAYS)
        if now >= unblock:
            return None
        return unblock

    @classmethod
    def member_has_active_stint(cls, username: str) -> bool:
        """One stint at a time per member.

        "Active" here = not removed and not moved to purgatory. An expired-
        but-not-removed stint still blocks a new one; the warden has to
        resolve the old one (mark removed or move to purgatory) before the
        member starts a new project.
        """
        return cls.objects.filter(
            username=username,
            removed_at__isnull=True,
            moved_to_purgatory_at__isnull=True,
        ).exists()


class ProjectStorageEvent(models.Model):
    """Append-only audit log of state transitions on a stint."""

    EVENT_CREATED = "created"
    EVENT_SCANNED = "scanned"
    EVENT_NOTICE_SENT = "notice_sent"
    EVENT_MOVED_TO_PURGATORY = "moved_to_purgatory"
    EVENT_REMOVED = "removed"
    EVENT_NOTE_ADDED = "note_added"

    EVENT_CHOICES = [
        (EVENT_CREATED, "Created"),
        (EVENT_SCANNED, "Scanned"),
        (EVENT_NOTICE_SENT, "Violation notice sent"),
        (EVENT_MOVED_TO_PURGATORY, "Moved to purgatory"),
        (EVENT_REMOVED, "Removed"),
        (EVENT_NOTE_ADDED, "Note added"),
    ]

    stint = models.ForeignKey(
        ProjectStorageStint,
        related_name="events",
        on_delete=models.CASCADE,
    )
    event_type = models.CharField(max_length=32, choices=EVENT_CHOICES)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    actor_label = models.CharField(
        max_length=120,
        blank=True,
        help_text="Free-text actor label for kiosk / self-service events where "
        "no Django user is logged in (e.g. 'kiosk: member self-issue').",
    )
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["stint", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.stint.stint_id} · {self.event_type} @ {self.created_at:%Y-%m-%d}"
