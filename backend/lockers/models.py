"""Models for the Lockers app (gh ForgeKey expansion, Phase 1+2).

A Locker is a physical, lockable enclosure — typically a SIG-owned cabinet
or storage drawer — that ForgeKey gates with one or more ESP32 devices.
The locker tracks its location, power source, and (optionally) the asset
currently stored inside, plus a "high-trust" flag that requires a
SIG-validated sign-off when the asset is returned.

Phase 1+2 covers:

- The ``Locker`` entity itself
- ``LockerOtp`` — short-lived, single-use 6-8 digit access codes
- The link from a Locker to one or more ``ESP32Device`` rows in the
  ForgeKey app via ``LockerDevice``
- The certification-required relationship (M2M to
  ``membership.Certification``)

Phase 3 (MQTT integration), Phase 4 (audit + state machine), and
Phase 5 (Celery janitors) extend this model layer with
``LockerAccessEvent`` and ``LockerHighTrustReturn`` tables.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class PowerSource(models.TextChoices):
    """How the locker hardware is powered.

    Drives runbook expectations: an unpowered locker (mechanical-only
    keypad) cannot publish telemetry, so dashboards skip its
    propped-door + lockout polling.
    """

    POE = "poe", "Power over Ethernet"
    USB = "usb", "USB-C / barrel jack"
    AC_OUTLET = "ac_outlet", "AC mains outlet"
    BATTERY = "battery", "Battery only"
    UNPOWERED = "unpowered", "Unpowered (mechanical only)"


class Locker(models.Model):
    """A physical lockable enclosure controlled by ForgeKey.

    The asset currently stored inside is tracked via ``current_asset``
    so the locker dashboard can answer "what's in locker X" without a
    join-walk through usage logs. Mutating ``current_asset`` is the
    responsibility of the access flow (Phase 4) — the model itself
    just persists the pointer.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(
        max_length=200,
        help_text="Human-readable locker name (e.g. 'Wood Shop locker 4').",
    )
    slug = models.SlugField(
        max_length=120,
        unique=True,
        help_text="Stable identifier used in URLs and topics.",
    )
    location = models.ForeignKey(
        "inventory.Location",
        on_delete=models.PROTECT,
        related_name="lockers",
        help_text="Physical location housing this locker.",
    )
    owning_sig = models.ForeignKey(
        Group,
        on_delete=models.PROTECT,
        related_name="lockers",
        help_text=(
            "SIG (Group) that owns this locker. SIG admins can grant access, "
            "configure required certifications, and accept high-trust returns."
        ),
    )
    description = models.TextField(
        blank=True,
        help_text="Optional notes — what this locker holds, special handling, etc.",
    )
    power_source = models.CharField(
        max_length=20,
        choices=PowerSource.choices,
        default=PowerSource.POE,
        help_text="How the locker is powered.",
    )
    current_asset = models.ForeignKey(
        "inventory.Asset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="storing_lockers",
        help_text=(
            "Asset currently stored in this locker. Null when the locker is "
            "empty or pending a high-trust return acceptance."
        ),
    )
    is_high_trust = models.BooleanField(
        default=False,
        help_text=(
            "If true, returns require a validated SIG admin to sign off "
            "before another user can take the asset. Surfaces a "
            "PendingHighTrustReturn row that blocks new OTPs (Phase 4)."
        ),
    )
    led_count = models.PositiveSmallIntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        help_text=(
            "Number of WS2818 LEDs inside the locker for user-facing "
            "status cues (idle / unlocking / lockout). 0 = no LED strip "
            "wired."
        ),
    )
    required_certifications = models.ManyToManyField(
        "membership.Certification",
        blank=True,
        related_name="lockers",
        help_text=(
            "Certifications a user must hold to receive an OTP for this "
            "locker (operator/SIG bypass still applies)."
        ),
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive lockers reject new OTP requests but stay in audit history.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["location__name", "name"]
        indexes = [
            models.Index(fields=["location", "is_active"]),
            models.Index(fields=["owning_sig", "is_active"]),
            models.Index(fields=["current_asset"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} @ {self.location.name}"


class LockerDevice(models.Model):
    """Link table between Locker and one or more `forgekey.ESP32Device`s.

    A locker can have multiple devices: a latch controller, a
    door-state reed switch, an IR-break sensor, a keypad. Each gets its
    own row with a `role` so the locker dashboard can render them
    distinctly.
    """

    ROLE_LATCH = "latch"
    ROLE_REED_SWITCH = "reed_switch"
    ROLE_IR_BREAK = "ir_break"
    ROLE_KEYPAD = "keypad"
    ROLE_LED_STRIP = "led_strip"
    ROLE_MORTISE_KEY = "mortise_key"

    ROLE_CHOICES = [
        (ROLE_LATCH, "Latch controller"),
        (ROLE_REED_SWITCH, "Door reed switch"),
        (ROLE_IR_BREAK, "Inventory IR break sensor"),
        (ROLE_KEYPAD, "OTP keypad"),
        (ROLE_LED_STRIP, "WS2818 LED strip controller"),
        (ROLE_MORTISE_KEY, "Mortise key (admin override) sensor"),
    ]

    locker = models.ForeignKey(
        Locker,
        on_delete=models.CASCADE,
        related_name="device_assignments",
        help_text="Which locker this device serves.",
    )
    device = models.ForeignKey(
        "forgekey.ESP32Device",
        on_delete=models.CASCADE,
        related_name="locker_assignments",
        help_text="The ESP32Device row.",
    )
    role = models.CharField(
        max_length=30,
        choices=ROLE_CHOICES,
        help_text="What this device does for the locker.",
    )
    is_primary = models.BooleanField(
        default=False,
        help_text=(
            "Primary controller for this role on this locker. Exactly one "
            "primary per (locker, role) when present."
        ),
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["locker", "role"]
        constraints = [
            models.UniqueConstraint(
                fields=["locker", "device", "role"],
                name="unique_locker_device_role",
            ),
        ]
        indexes = [
            models.Index(fields=["locker", "role"]),
        ]

    def __str__(self) -> str:
        return f"{self.locker.name} :: {self.get_role_display()}"


def _generate_otp_code(digits: int = 6) -> str:
    """Cryptographically random N-digit code. Uses ``secrets.choice`` so
    the result is suitable for a one-time access credential.
    """
    if digits < 6 or digits > 8:
        raise ValueError("OTP code length must be 6, 7, or 8 digits")
    return "".join(secrets.choice("0123456789") for _ in range(digits))


class LockerOtp(models.Model):
    """One-time access PIN for a locker.

    Generated when a user requests access. Lives for a short TTL (60
    minutes per spec). Single-use: ``mark_used`` clears it. Can be
    administratively revoked.
    """

    DEFAULT_TTL = timedelta(minutes=60)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    locker = models.ForeignKey(
        Locker,
        on_delete=models.CASCADE,
        related_name="otps",
        help_text="Locker this code unlocks.",
    )
    requesting_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="locker_otps",
        help_text="User who requested the OTP — accountability anchor.",
    )
    code = models.CharField(
        max_length=8,
        help_text=(
            "6-8 digit access code. Stored in cleartext because the row "
            "itself is the bearer credential and lives < 60 minutes; "
            "verifying against a hash would force the keypad to relay "
            "every guess back to Django."
        ),
    )
    expires_at = models.DateTimeField(
        help_text="When this OTP stops being valid. Spec: T+60 minutes.",
    )
    used_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the OTP was redeemed (single-use).",
    )
    revoked_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the OTP was administratively revoked before use.",
    )
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="locker_otps_revoked",
        help_text="Admin who revoked this OTP (if revoked_at is set).",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # Hot lookup: find an active OTP by (locker, code).
            models.Index(fields=["locker", "code", "expires_at"]),
            models.Index(fields=["requesting_user", "created_at"]),
        ]
        constraints = [
            # Prevent two simultaneously-active OTPs with the same code on
            # the same locker. Enforced only when neither used nor revoked.
            models.UniqueConstraint(
                fields=["locker", "code"],
                condition=models.Q(used_at__isnull=True, revoked_at__isnull=True),
                name="unique_active_locker_otp_code",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.locker.name}/{self.code} [{self.state}]"

    @property
    def state(self) -> str:
        if self.used_at is not None:
            return "used"
        if self.revoked_at is not None:
            return "revoked"
        if timezone.now() >= self.expires_at:
            return "expired"
        return "active"

    @property
    def is_redeemable(self) -> bool:
        return self.used_at is None and self.revoked_at is None and timezone.now() < self.expires_at

    def mark_used(self) -> None:
        """Record successful redemption. Idempotent on already-used."""
        if self.used_at is not None:
            return
        self.used_at = timezone.now()
        self.save(update_fields=["used_at"])

    def revoke(self, *, by) -> None:
        """Administratively revoke this OTP. Idempotent on already-revoked."""
        if self.revoked_at is not None:
            return
        self.revoked_at = timezone.now()
        self.revoked_by = by
        self.save(update_fields=["revoked_at", "revoked_by"])


class LockerAccessEventKind(models.TextChoices):
    """What happened on the locker (Phase 4 audit state machine).

    Phase 3's webhook receivers (`views.LockoutEventView`, etc.) persist
    one of these per inbound EMQX forward. Phase 5's janitors flip
    ``OPEN`` rows to ``INCOMPLETE`` when they overstay the propped-door
    threshold.
    """

    REQUESTED = "requested", "Access requested (OTP minted)"
    UNLOCKED = "unlocked", "Latch released"
    OPEN = "open", "Door opened"
    SECURED = "secured", "Door closed + latch confirmed"
    INCOMPLETE = "incomplete", "Door left open past threshold"
    TAMPER = "tamper", "Tamper / lockout entered"
    LOCKOUT_CLEARED = "lockout_cleared", "Lockout cleared"
    ITEM_REMOVED = "item_removed", "Inventory IR break while door open"
    ADMIN_PHYSICAL_ENTRY = "admin_physical_entry", "Mortise key (manual override)"


class LockerAccessEvent(models.Model):
    """Append-only audit row for every meaningful locker event.

    This is the join surface that operators look at to ask "what happened
    on locker X today?" — both the high-trust return flow and the
    propped-door alert task lean on this table. Rows are written by:

    - ``services.access.generate_otp`` (kind=REQUESTED)
    - ``services.commands.publish_unlock`` (kind=UNLOCKED)
    - ``views.ReedStatusEventView`` (kind=OPEN | SECURED)
    - ``views.IrBreakEventView`` (kind=ITEM_REMOVED) when correlated with
      an OPEN reed-state on the same locker
    - ``views.LockoutEventView`` (kind=TAMPER | LOCKOUT_CLEARED)
    - ``mqtt_consumer`` mortise-key handler (kind=ADMIN_PHYSICAL_ENTRY)
    - ``tasks.flag_propped_doors`` (kind=INCOMPLETE)

    The model is intentionally append-only — historical rows never get
    rewritten when the operator-facing state changes. Phase 5 closes a
    cycle by writing a new SECURED row, not by mutating the original
    OPEN row.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    locker = models.ForeignKey(
        Locker,
        on_delete=models.CASCADE,
        related_name="access_events",
        help_text="Locker this event is about.",
    )
    kind = models.CharField(
        max_length=24,
        choices=LockerAccessEventKind.choices,
        help_text="What happened.",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="locker_access_events",
        help_text=(
            "User responsible (the OTP requester for unlocks; the admin "
            "for clear_lockout; null for firmware-originated rows where "
            "no user is associated, e.g. propped-door janitor)."
        ),
    )
    otp = models.ForeignKey(
        LockerOtp,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="access_events",
        help_text=(
            "OTP that authorised this event. Null for direct staff "
            "unlocks, mortise-key entries, or tamper events."
        ),
    )
    asset = models.ForeignKey(
        "inventory.Asset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="locker_events",
        help_text=(
            "Asset implicated by the event (the locker's current_asset "
            "snapshot at event time; null when the locker is empty)."
        ),
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Event-specific payload (raw EMQX forward, propped-door "
            "duration, lockout reason, etc.). Already passes through "
            "``observability_redaction.redact`` before storage."
        ),
    )
    occurred_at = models.DateTimeField(
        default=timezone.now,
        help_text="When the event occurred (broker-supplied if available).",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["locker", "-occurred_at"]),
            models.Index(fields=["kind", "-occurred_at"]),
            models.Index(fields=["actor", "-occurred_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.locker.slug} {self.kind} @ {self.occurred_at:%Y-%m-%d %H:%M}"


class LockerHighTrustReturn(models.Model):
    """Pending sign-off for a high-trust locker return (Phase 4).

    When a user closes a high-trust locker after replacing the asset, a
    SIG admin must confirm the asset is in acceptable condition before
    another user can take it. This model gates that flow:

    - Created by the SECURED handler when the locker is high-trust.
    - Resolved by ``accept`` (the asset is good; locker is available
      again) or ``reject`` (the asset is damaged / missing; the locker
      is locked out until staff intervenes).
    """

    STATUS_PENDING = "pending"
    STATUS_ACCEPTED = "accepted"
    STATUS_REJECTED = "rejected"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending acceptance"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_REJECTED, "Rejected"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    locker = models.ForeignKey(
        Locker,
        on_delete=models.CASCADE,
        related_name="high_trust_returns",
    )
    returning_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="high_trust_returns_made",
        help_text="User who returned the asset.",
    )
    asset = models.ForeignKey(
        "inventory.Asset",
        on_delete=models.PROTECT,
        related_name="high_trust_returns",
    )
    triggering_event = models.ForeignKey(
        LockerAccessEvent,
        on_delete=models.PROTECT,
        related_name="high_trust_returns",
        help_text="The SECURED event that opened this return.",
    )
    status = models.CharField(
        max_length=12,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    resolution_notes = models.TextField(blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="high_trust_returns_resolved",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["locker", "status"]),
            models.Index(fields=["status", "-created_at"]),
        ]
        constraints = [
            # At most one *pending* return per locker — the locker is
            # frozen until it's resolved.
            models.UniqueConstraint(
                fields=["locker"],
                condition=models.Q(status="pending"),
                name="unique_pending_high_trust_return_per_locker",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.locker.slug} return {self.status} ({self.asset.name})"

    def accept(self, *, by, notes: str = "") -> None:
        if self.status != self.STATUS_PENDING:
            return
        self.status = self.STATUS_ACCEPTED
        self.resolved_by = by
        self.resolved_at = timezone.now()
        if notes:
            self.resolution_notes = notes
        self.save(update_fields=["status", "resolved_by", "resolved_at", "resolution_notes"])

    def reject(self, *, by, notes: str = "") -> None:
        if self.status != self.STATUS_PENDING:
            return
        self.status = self.STATUS_REJECTED
        self.resolved_by = by
        self.resolved_at = timezone.now()
        if notes:
            self.resolution_notes = notes
        self.save(update_fields=["status", "resolved_by", "resolved_at", "resolution_notes"])
