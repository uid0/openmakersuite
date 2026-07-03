"""
RFID-KeyMaster tool-interlock integration models (Phase 1, backend only).

OMS is the control plane + credential vault for remotely enabling/disabling
Dallas Makerspace RFID-KeyMaster tool interlocks. It never talks to an
interlock directly (they live on the shop LAN behind the firewall). Instead
the claim-printer Pi polls a command-queue here and SSH-executes an on-device
helper against the target (later phases). This module is device-independent:
it stores the connection facts + encrypted SSH credentials and models the
enable/disable/status command queue and the status the Pi reports back.

Two persisted shapes:

* ``Interlock`` — one row per managed tool, linked to an inventory ``Asset``.
  Holds host/port + SSH username and the **Fernet-encrypted** SSH password
  (never plaintext on disk or in the API), the systemd service name, the
  relay pin/interface the on-device helper drives, the operator's
  ``desired_state`` and the last state/telemetry the Pi reported.
* ``InterlockCommand`` — the enable/disable/status work queue the Pi polls.

Security: the SSH password is encrypted at rest with a Fernet KEK
(:func:`_fernet`), is write-only in the admin API (see serializers), and is
only ever decrypted for the authenticated Pi-executor command-queue endpoint.
It is never logged in plaintext.
"""

from __future__ import annotations

import base64
import hashlib

from django.conf import settings
from django.db import models

from cryptography.fernet import Fernet

# ---------- SSH-credential encryption ----------------------------------------
#
# Mirrors the Fernet-at-rest pattern used for BMS OAuth tokens
# (``bms.models._fernet``): a 32-byte urlsafe-base64 KEK derived by SHA-256,
# domain-separated so a rotation of one secret does not invalidate unrelated
# ciphertext. We prefer a dedicated ``INTERLOCK_SECRET_KEY`` when configured
# so rotating Django's ``SECRET_KEY`` does not orphan every stored SSH
# password (unlike OAuth tokens, SSH creds cannot be silently re-issued — an
# operator would have to re-enter them). If it is unset we fall back to
# ``SECRET_KEY`` so development and tests work with zero configuration.


def _interlock_kek_bytes() -> bytes:
    """Return the 32-byte urlsafe-base64 Fernet key for SSH-credential encryption.

    Accepts any string as the source secret (it is hashed, not used raw), so an
    operator can set ``INTERLOCK_SECRET_KEY`` to any high-entropy value without
    having to generate a valid Fernet key.
    """
    dedicated = (getattr(settings, "INTERLOCK_SECRET_KEY", "") or "").strip()
    secret = dedicated or (getattr(settings, "SECRET_KEY", "") or "")
    digest = hashlib.sha256(b"interlocks.ssh.v1|" + secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet() -> Fernet:
    return Fernet(_interlock_kek_bytes())


def _encrypt(plain: str) -> bytes:
    if not plain:
        return b""
    return _fernet().encrypt(plain.encode("utf-8"))


def _decrypt(blob: bytes) -> str:
    if not blob:
        return ""
    return _fernet().decrypt(bytes(blob)).decode("utf-8")


# ---------- models -----------------------------------------------------------


class Interlock(models.Model):
    """A remotely managed RFID-KeyMaster tool interlock, bound to an Asset."""

    AUTH_PASSWORD = "password"
    AUTH_KEY = "key"
    AUTH_TYPE_CHOICES = [
        (AUTH_PASSWORD, "SSH password"),
        # SSH key auth is planned but not implemented yet; the field exists so
        # migrating a device is a config flip rather than a schema change.
        (AUTH_KEY, "SSH key (not implemented)"),
    ]

    STATE_ENABLED = "enabled"
    STATE_DISABLED = "disabled"
    STATE_CHOICES = [
        (STATE_ENABLED, "Enabled"),
        (STATE_DISABLED, "Disabled"),
    ]

    INTERFACE_PIGPIO = "pigpio"
    INTERFACE_PIFACE = "piface"
    INTERFACE_AUTOMATIONPHAT = "automationphat"
    RELAY_INTERFACE_CHOICES = [
        (INTERFACE_PIGPIO, "pigpio"),
        (INTERFACE_PIFACE, "PiFace"),
        (INTERFACE_AUTOMATIONPHAT, "Automation pHAT"),
    ]

    label = models.CharField(
        max_length=120,
        help_text="Operator-visible name for this interlock (e.g. 'Laser cutter').",
    )
    asset = models.ForeignKey(
        "inventory.Asset",
        on_delete=models.CASCADE,
        related_name="interlocks",
        help_text="The physical tool this interlock powers.",
    )

    # --- connection facts (how the Pi reaches the device) --------------------
    host = models.CharField(max_length=255, help_text="IP address or hostname on the shop LAN.")
    ssh_port = models.PositiveIntegerField(default=22)
    ssh_username = models.CharField(max_length=64, default="root")
    # Fernet ciphertext — never plaintext on disk, never returned by the API.
    encrypted_ssh_password = models.BinaryField(blank=True, default=b"")
    auth_type = models.CharField(
        max_length=16,
        choices=AUTH_TYPE_CHOICES,
        default=AUTH_PASSWORD,
    )

    # --- on-device facts (what the helper script acts on) --------------------
    service_name = models.CharField(
        max_length=128,
        default="keymaster.service",
        help_text="systemd unit the on-device helper starts/stops.",
    )
    relay_pin = models.IntegerField(
        null=True,
        blank=True,
        help_text="Relay GPIO/output pin the helper forces off on disable.",
    )
    relay_interface = models.CharField(
        max_length=16,
        choices=RELAY_INTERFACE_CHOICES,
        null=True,
        blank=True,
        help_text="Which relay backend the on-device helper drives.",
    )

    # --- state ----------------------------------------------------------------
    desired_state = models.CharField(
        max_length=16,
        choices=STATE_CHOICES,
        default=STATE_ENABLED,
        help_text="What the operator wants; enqueued commands drive the device to this.",
    )
    last_reported_state = models.CharField(
        max_length=16,
        choices=STATE_CHOICES,
        null=True,
        blank=True,
        help_text="State the Pi last reported for the device.",
    )
    in_use = models.BooleanField(
        null=True,
        blank=True,
        help_text="Whether the tool is drawing current (CurrentSense); null if unknown.",
    )
    online = models.BooleanField(
        default=False,
        help_text="Whether the Pi could reach the device on its last report.",
    )
    last_seen_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["label"]

    def __str__(self) -> str:
        return f"{self.label} ({self.host})"

    # --- SSH password accessors ---------------------------------------------
    def set_ssh_password(self, plain: str) -> None:
        """Encrypt and store ``plain`` (in memory only; caller must save())."""
        self.encrypted_ssh_password = _encrypt(plain or "")

    def get_ssh_password(self) -> str:
        """Decrypt the stored SSH password. Only call for the Pi-executor path."""
        return _decrypt(bytes(self.encrypted_ssh_password))

    @property
    def has_credentials(self) -> bool:
        """True when an SSH password is stored — exposed instead of the secret."""
        return bool(self.encrypted_ssh_password)


class InterlockCommand(models.Model):
    """A queued enable/disable/status command the Pi executor polls and runs."""

    ACTION_ENABLE = "enable"
    ACTION_DISABLE = "disable"
    ACTION_STATUS = "status"
    ACTION_CHOICES = [
        (ACTION_ENABLE, "Enable"),
        (ACTION_DISABLE, "Disable"),
        (ACTION_STATUS, "Status"),
    ]

    STATE_PENDING = "pending"
    STATE_CLAIMED = "claimed"
    STATE_DONE = "done"
    STATE_FAILED = "failed"
    STATE_CHOICES = [
        (STATE_PENDING, "Pending"),
        (STATE_CLAIMED, "Claimed"),
        (STATE_DONE, "Done"),
        (STATE_FAILED, "Failed"),
    ]

    interlock = models.ForeignKey(
        Interlock,
        on_delete=models.CASCADE,
        related_name="commands",
    )
    action = models.CharField(max_length=16, choices=ACTION_CHOICES)
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default=STATE_PENDING)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="interlock_commands",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    result_text = models.TextField(blank=True, default="")
    success = models.BooleanField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.action} {self.interlock_id} [{self.state}]"
