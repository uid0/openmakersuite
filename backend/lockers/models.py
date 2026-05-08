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
