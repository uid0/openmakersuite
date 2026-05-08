"""High-level locker command publishers (Phase 3).

Wraps the generic `forgekey.services.device_commands.publish_command`
with locker-aware behaviour:

- Picks the right ESP32Device for a given role (latch, LED strip).
- Mints a short-TTL ES256 JWT via
  `forgekey.services.jwt_signing.make_command_jwt` so firmware can
  verify the command before acting on it.
- Emits an audit row through the existing forgekey audit channel
  using `audit_action` so the unified review surface
  (`gh #359`) can correlate locker events with other ForgeKey
  activity.

Phase 4 layers a `LockerAccessEvent` on top of these calls; Phase 5
adds Celery janitors for OTP cleanup, propped-door alerts, and
lockout reset.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from forgekey.models import ESP32Device
from forgekey.services.device_commands import (
    DeviceCommandError,
    publish_command,
)
from forgekey.services.jwt_signing import make_command_jwt

from ..models import Locker, LockerDevice, LockerOtp

logger = logging.getLogger(__name__)


class NoSuchLockerDevice(LookupError):
    """Raised when a Locker has no ESP32Device registered for the given role."""


def _resolve_device(locker: Locker, role: str) -> ESP32Device:
    """Return the *primary* ESP32Device for `(locker, role)`, falling back
    to the most recently created link if no primary is set.
    """
    qs = (
        LockerDevice.objects.filter(locker=locker, role=role)
        .select_related("device")
        .order_by("-is_primary", "-created_at")
    )
    link = qs.first()
    if link is None:
        raise NoSuchLockerDevice(f"Locker {locker.slug!r} has no device assigned for role {role!r}")
    return link.device


def publish_unlock(
    *,
    locker: Locker,
    otp: Optional[LockerOtp] = None,
    actor: Optional[Any] = None,
    ttl_seconds: int = 60,
    client: Any = None,
) -> str:
    """Publish an `unlock` command to the locker's latch device.

    Payload shape: `{"cmd": "unlock", "jwt": "<ES256 JWT>", "otp_id": "..."}`.
    The JWT carries `{mac, cmd, exp}` and is verified by firmware
    against the EMQX device-auth public key.

    `otp_id` (optional) is the LockerOtp.id that authorised this
    unlock; firmware can include it on the resulting telemetry so
    Phase 4's audit correlation has a deterministic anchor.

    `actor` is logged by the underlying `publish_command` audit so
    operator-initiated unlocks (without an OTP) stay traceable.

    Returns the MQTT topic the message was published on. Raises
    :class:`NoSuchLockerDevice` if the locker has no latch role
    configured, or :class:`DeviceCommandError` on broker rejection.
    """
    device = _resolve_device(locker, LockerDevice.ROLE_LATCH)
    jwt = make_command_jwt(mac=device.mac_address, cmd="unlock", exp_seconds=ttl_seconds)
    payload: dict[str, Any] = {"cmd": "unlock", "jwt": jwt}
    if otp is not None:
        payload["otp_id"] = str(otp.id)
    return publish_command(
        device,
        payload,
        actor=actor,
        audit_action="locker_unlock",
        client=client,
    )


def publish_lockout(
    *,
    locker: Locker,
    reason: str,
    actor: Optional[Any] = None,
    ttl_seconds: int = 60,
    client: Any = None,
) -> str:
    """Publish a `lockout` command — latch refuses any unlock until cleared.

    The reason is included in the audit-line payload so operators can
    see *why* a locker entered fail-secure state. The latch
    behaviour itself is firmware-defined (Phase 6).
    """
    device = _resolve_device(locker, LockerDevice.ROLE_LATCH)
    jwt = make_command_jwt(mac=device.mac_address, cmd="lockout", exp_seconds=ttl_seconds)
    return publish_command(
        device,
        {"cmd": "lockout", "jwt": jwt, "reason": reason},
        actor=actor,
        audit_action="locker_lockout",
        client=client,
    )


def publish_clear_lockout(
    *,
    locker: Locker,
    actor: Optional[Any] = None,
    ttl_seconds: int = 60,
    client: Any = None,
) -> str:
    """Publish a `clear_lockout` command — release the fail-secure state.

    Restricted at the call site (Phase 5 wires this to an admin-only
    Celery task). The publisher itself does not enforce
    authorization; calls in production must check
    `is_staff_or_sig_admin` (or equivalent) before invoking.
    """
    device = _resolve_device(locker, LockerDevice.ROLE_LATCH)
    jwt = make_command_jwt(mac=device.mac_address, cmd="clear_lockout", exp_seconds=ttl_seconds)
    return publish_command(
        device,
        {"cmd": "clear_lockout", "jwt": jwt},
        actor=actor,
        audit_action="locker_clear_lockout",
        client=client,
    )


def publish_init_ack(
    *,
    locker: Locker,
    client: Any = None,
) -> str:
    """Publish an `init_ack` command to stop the firmware's initialization
    LED pattern (Phase 3 — Initialization Handshake step).

    Hits the latch device by default since that's the canonical
    "primary" device for a locker. If the firmware exposes a
    different role for ack reception, swap to that.
    """
    device = _resolve_device(locker, LockerDevice.ROLE_LATCH)
    jwt = make_command_jwt(mac=device.mac_address, cmd="init_ack", exp_seconds=60)
    return publish_command(
        device,
        {"cmd": "init_ack", "jwt": jwt},
        audit_action="locker_init_ack",
        client=client,
    )


__all__ = (
    "DeviceCommandError",
    "NoSuchLockerDevice",
    "publish_clear_lockout",
    "publish_init_ack",
    "publish_lockout",
    "publish_unlock",
)
