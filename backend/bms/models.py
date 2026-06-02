"""
Building Management System (BMS) integration models.

Read-only v1 — OMS pulls current state from the BMS (e.g. Resideo /
Honeywell Home Pro) into per-thermostat bindings so the operator can see
indoor temp / setpoints alongside the manually-entered ``climate.Thermostat``
row. Write methods (set_setpoint) and the occupancy-driven decider arrive
in follow-up PRs.

Two persisted shapes:

* ``BmsConfig`` — one row per BMS account. Holds the OAuth access /
  refresh tokens (encrypted at rest with a Fernet KEK derived from
  Django's ``SECRET_KEY``, domain-separated from firmware signing so
  a SECRET_KEY rotation invalidates BMS tokens but not signing keys).
* ``ThermostatBinding`` — links a ``climate.Thermostat`` to a single
  external device on a single config. Carries the most recently fetched
  state so the admin / future decider can read it without hitting the
  upstream API every time.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from typing import Optional

from django.conf import settings
from django.db import models

from cryptography.fernet import Fernet

# ---------- token encryption -------------------------------------------------


def _bms_kek_bytes() -> bytes:
    """KEK for at-rest OAuth token encryption.

    Domain-separated from firmware signing's KEK (forgekey.services
    .firmware_signing._fernet) so a SECRET_KEY rotation invalidates only
    BMS tokens (which can be re-issued via the OAuth dance) and not
    unrelated Fernet uses.
    """
    secret = getattr(settings, "SECRET_KEY", "") or ""
    digest = hashlib.sha256(b"bms.oauth.v1|" + secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet() -> Fernet:
    return Fernet(_bms_kek_bytes())


def _encrypt(plain: str) -> bytes:
    if not plain:
        return b""
    return _fernet().encrypt(plain.encode("utf-8"))


def _decrypt(blob: bytes) -> str:
    if not blob:
        return ""
    return _fernet().decrypt(bytes(blob)).decode("utf-8")


# ---------- models -----------------------------------------------------------


class BmsConfig(models.Model):
    """A single BMS account / integration.

    Per-account because Resideo OAuth is per-user — one tenant of OMS will
    typically have one BmsConfig for the staff Honeywell Home account that
    owns every shop thermostat. Multiple configs are supported for the
    multi-tenant / mixed-vendor cases that come later.
    """

    ADAPTER_RESIDEO = "resideo"
    ADAPTER_MOCK = "mock"
    ADAPTER_CHOICES = [
        (ADAPTER_RESIDEO, "Resideo / Honeywell Home Pro"),
        (ADAPTER_MOCK, "Mock (testing only)"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(
        max_length=120,
        unique=True,
        help_text="Operator-visible label. Pick something that tells you which "
        "physical building / account this is bound to.",
    )
    adapter_type = models.CharField(max_length=32, choices=ADAPTER_CHOICES)

    # OAuth state — Fernet ciphertext, never plaintext on disk or in admin.
    encrypted_access_token = models.BinaryField(blank=True, default=b"")
    encrypted_refresh_token = models.BinaryField(blank=True, default=b"")
    access_token_expires_at = models.DateTimeField(null=True, blank=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_sync_error = models.TextField(
        blank=True,
        help_text="Adapter exception message from the most recent sync. " "Empty on success.",
    )

    class Meta:
        ordering = ["name"]
        verbose_name = "BMS config"
        verbose_name_plural = "BMS configs"

    def __str__(self) -> str:
        return f"{self.name} [{self.adapter_type}]"

    # ----- token accessors --------------------------------------------------

    def access_token(self) -> str:
        return _decrypt(bytes(self.encrypted_access_token))

    def refresh_token(self) -> str:
        return _decrypt(bytes(self.encrypted_refresh_token))

    def set_tokens(
        self,
        *,
        access_token: str,
        refresh_token: Optional[str],
        expires_at,
    ) -> None:
        """Persist a fresh token pair from the OAuth exchange / refresh.

        ``refresh_token`` is optional because some providers omit it on
        refresh responses; in that case we keep the existing refresh
        token (operators can re-run the full auth dance if it's lost).
        """
        self.encrypted_access_token = _encrypt(access_token)
        if refresh_token:
            self.encrypted_refresh_token = _encrypt(refresh_token)
        self.access_token_expires_at = expires_at
        self.save(
            update_fields=[
                "encrypted_access_token",
                "encrypted_refresh_token",
                "access_token_expires_at",
                "updated_at",
            ]
        )


class ThermostatBinding(models.Model):
    """Link an OMS ``climate.Thermostat`` to a BMS device.

    State fields here are the last-known values pulled by ``bms_sync_all``.
    Polling cadence is whatever the operator's Celery beat says; consumers
    that want fresh data should call ``ResideoAdapter.get_state`` directly
    (and update the binding) rather than reading possibly-stale fields.
    """

    HVAC_MODE_OFF = "off"
    HVAC_MODE_HEAT = "heat"
    HVAC_MODE_COOL = "cool"
    HVAC_MODE_AUTO = "auto"
    HVAC_MODE_EMHEAT = "emheat"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    thermostat = models.OneToOneField(
        "climate.Thermostat",
        on_delete=models.PROTECT,
        related_name="bms_binding",
        help_text="The OMS-side thermostat row this BMS device backs.",
    )
    config = models.ForeignKey(
        BmsConfig,
        on_delete=models.PROTECT,
        related_name="bindings",
    )
    external_device_id = models.CharField(
        max_length=128,
        help_text="The BMS-side device identifier (Resideo deviceID).",
    )
    external_location_id = models.CharField(
        max_length=128,
        blank=True,
        help_text="The BMS-side location identifier — Resideo requires "
        "locationId on every per-device call.",
    )

    # Last-known state. Nullable because we may not have synced yet, or
    # the device may not report a given field (humidity for thermostats
    # that lack a sensor).
    indoor_temp_f = models.FloatField(null=True, blank=True)
    indoor_humidity_pct = models.FloatField(null=True, blank=True)
    cool_setpoint_f = models.FloatField(null=True, blank=True)
    heat_setpoint_f = models.FloatField(null=True, blank=True)
    hvac_mode = models.CharField(max_length=20, blank=True)
    fan_mode = models.CharField(max_length=20, blank=True)
    state_raw = models.JSONField(
        blank=True,
        default=dict,
        help_text="Full adapter response on the most recent sync — handy "
        "for debugging field-mapping issues against new device models.",
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_sync_error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["thermostat__location__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["config", "external_device_id"],
                name="bms_binding_unique_config_device",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.thermostat} ↔ {self.config.name}/{self.external_device_id}"
