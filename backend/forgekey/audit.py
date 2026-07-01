"""Audit-event recording for ForgeKey device actions (gh #352 / #334).

Centralized helper so emission sites (views, services, MQTT handlers) all go
through one path. Keeps the model insert + ``related_name`` resolution +
sensible defaults in one place; callers stay terse.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from django.contrib.auth import get_user_model

from .models import (
    AssetAuthorization,
    DeviceFirmwareUpdate,
    DeviceLockout,
    ESP32Device,
    ForgeKeyAuditEvent,
)

User = get_user_model()


def record_event(
    *,
    action: str,
    actor: Optional[User] = None,
    authorization: Optional[AssetAuthorization] = None,
    lockout: Optional[DeviceLockout] = None,
    firmware_update: Optional[DeviceFirmwareUpdate] = None,
    device: Optional[ESP32Device] = None,
    asset: Optional[Any] = None,
    notes: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> ForgeKeyAuditEvent:
    """Record a ForgeKey audit event.

    At least one of ``authorization``, ``lockout``, ``firmware_update``,
    ``device``, or ``asset`` must be supplied so the row is queryable by
    entity. When ``asset`` is not passed explicitly it is auto-derived from the
    supplied entity when possible (authorizations and lockouts both have
    ``asset`` references). Access-control events (a DENY for an unauthorized or
    unknown card) carry no authorization row, so they pass ``asset`` directly.

    Anonymous / system-initiated actions are allowed (``actor=None``); the
    audit row simply records "no known user." Caller is expected to pass the
    request user when one exists.
    """
    if asset is None:
        if authorization is not None:
            asset = authorization.asset
        elif lockout is not None:
            asset = lockout.asset

    return ForgeKeyAuditEvent.objects.create(
        action=action,
        actor=actor if (actor is not None and getattr(actor, "is_authenticated", False)) else None,
        asset=asset,
        device=device,
        authorization=authorization,
        lockout=lockout,
        firmware_update=firmware_update,
        notes=notes,
        metadata=metadata or {},
    )
