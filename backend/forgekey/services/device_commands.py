"""
Server → device command publishing over MQTT.

This module wraps the paho client used by ``forgekey.tasks`` so that the
DRF endpoints, the firmware dispatch service, and the long-running
``mqtt_consumer`` can all share a single broker connection. It also writes
a structured audit log line on every successful publish — the JSON shape
makes it straightforward to grep production logs for *who* sent *what* to
which device.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Tuple

from django.utils import timezone

import paho.mqtt.client as mqtt

from ..models import DeviceCommand, ESP32Device
from ..tasks import get_mqtt_client
from ..utils import get_mqtt_command_topic

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("forgekey.audit")


class DeviceCommandError(RuntimeError):
    """Raised when the broker rejects a publish."""


PUBACK_WAIT_SECONDS = 2.0


def publish_command(
    device: ESP32Device,
    command_payload: Dict[str, Any],
    *,
    actor: Optional[Any] = None,
    audit_action: Optional[str] = None,
    client: Optional[mqtt.Client] = None,
) -> str:
    """Publish a JSON command payload to a device's MQTT command topic.

    Waits up to ``PUBACK_WAIT_SECONDS`` for the broker PUBACK before
    returning so callers get "broker has the message" rather than just
    "queued in the local paho buffer". Returns the topic the message
    was published on. Raises :class:`DeviceCommandError` on broker
    rejection or PUBACK timeout so callers can map it to a retryable
    failure.

    Note: a successful return still does not guarantee the device
    received the command — that's confirmed asynchronously via
    ``cmd_ack`` on the device's status topic and tracked on the
    :class:`DeviceCommand` row.
    """

    topic = get_mqtt_command_topic(device.mac_address)
    payload = dict(command_payload)
    payload.setdefault("timestamp", timezone.now().isoformat())

    body = json.dumps(payload)
    broker = client or get_mqtt_client()
    result = broker.publish(topic, body, qos=1)
    rc = getattr(result, "rc", 0)
    if rc != mqtt.MQTT_ERR_SUCCESS:
        logger.error(
            "Command publish failed: device=%s topic=%s rc=%s payload=%s",
            device.mac_address,
            topic,
            rc,
            payload,
        )
        raise DeviceCommandError(f"MQTT publish failed (rc={rc})")

    # Block briefly for the broker PUBACK. paho's wait_for_publish blocks
    # the calling thread until the broker confirms (QoS 1) or the timeout
    # fires; a False return signals the broker dropped or never confirmed.
    wait_for_publish = getattr(result, "wait_for_publish", None)
    if callable(wait_for_publish):
        try:
            acknowledged = wait_for_publish(timeout=PUBACK_WAIT_SECONDS)
        except (RuntimeError, ValueError):
            # Older paho releases raise instead of returning False — treat
            # as "broker never PUBACK'd" rather than letting it bubble up.
            acknowledged = False
        # paho < 2.0 returns None (no signal); only fail on explicit False.
        if acknowledged is False:
            logger.error(
                "Command publish broker PUBACK timeout: device=%s topic=%s",
                device.mac_address,
                topic,
            )
            raise DeviceCommandError(f"MQTT broker did not PUBACK within {PUBACK_WAIT_SECONDS}s")

    actor_id = None
    actor_username = None
    if actor is not None:
        actor_id = getattr(actor, "id", None) or getattr(actor, "pk", None)
        actor_username = getattr(actor, "username", None)

    audit_logger.info(
        json.dumps(
            {
                "event": "forgekey.device_command",
                "action": audit_action or payload.get("cmd"),
                "device_id": str(device.id),
                "device_mac": device.mac_address,
                "topic": topic,
                "payload": payload,
                "actor_id": str(actor_id) if actor_id is not None else None,
                "actor_username": actor_username,
                "at": payload["timestamp"],
            }
        )
    )
    return topic


def dispatch_command(
    device: ESP32Device,
    command_payload: Dict[str, Any],
    *,
    actor: Optional[Any] = None,
    audit_action: Optional[str] = None,
    client: Optional[mqtt.Client] = None,
) -> Tuple[str, DeviceCommand]:
    """Persist a :class:`DeviceCommand` row and publish it to the broker.

    Wraps :func:`publish_command` so callers (DRF endpoints, future task
    triggers) get both the audit row needed for live ack tracking and the
    broker-side publish in one call. The ``command_id`` injected into the
    payload is what the firmware echoes back on its status topic to mark
    the row as acked.

    The row is created *before* the publish and deleted on broker failure
    so the table never accumulates phantom commands that were never sent.
    """
    actor_user = (
        actor if (actor is not None and getattr(actor, "is_authenticated", False)) else None
    )
    payload = dict(command_payload)

    record = DeviceCommand.objects.create(
        device=device,
        command=audit_action or payload.get("cmd") or "unknown",
        payload={},
        sent_by=actor_user,
    )
    payload["command_id"] = str(record.id)

    try:
        topic = publish_command(
            device,
            payload,
            actor=actor,
            audit_action=audit_action,
            client=client,
        )
    except DeviceCommandError:
        record.delete()
        raise

    # Persist the final payload (with timestamp + command_id) for the audit row.
    record.payload = payload
    record.save(update_fields=["payload"])
    return topic, record


def _resolve_ack_status(status: str) -> str:
    normalized = (status or "").lower()
    if normalized in ("ok", "success", "acked", "complete", "completed", "done"):
        return DeviceCommand.ACK_OK
    if normalized in ("error", "failed", "failure", "rejected", "unknown_command"):
        return DeviceCommand.ACK_ERROR
    # Treat anything else as an error so the UI surfaces the firmware
    # message rather than silently leaving the command pending.
    return DeviceCommand.ACK_ERROR


def apply_command_ack(
    *,
    command_id: str,
    status: str,
    ack_payload: Optional[Dict[str, Any]] = None,
) -> bool:
    """Mark a :class:`DeviceCommand` as acked / errored from a status message.

    Returns ``True`` if a row was updated. Unknown command_ids are dropped
    (the firmware can ack a command issued by a previous deployment whose
    audit row was pruned).
    """
    if not command_id:
        return False
    rows = DeviceCommand.objects.filter(id=command_id, ack_status=DeviceCommand.ACK_PENDING).update(
        ack_status=_resolve_ack_status(status),
        ack_at=timezone.now(),
        ack_payload=ack_payload or None,
    )
    return rows > 0


def apply_command_ack_by_verb(
    *,
    device: Optional[ESP32Device],
    verb: str,
    status: str,
    ack_payload: Optional[Dict[str, Any]] = None,
) -> bool:
    """Best-effort ack when the firmware doesn't echo a ``command_id``.

    Matches the most-recent pending :class:`DeviceCommand` row for the
    given device whose ``command`` matches ``verb``. Used for the flat
    ``cmd_ack: "<verb>"`` snapshot form the current firmware emits.
    Returns ``True`` if a row was updated.

    Caveat: under a burst of two identical commands this collapses both
    into the older row. The object-form ack (``cmd_ack: {command_id, ...}``)
    is preferred and should be adopted by future firmware.
    """
    if device is None or not verb:
        return False
    pending = (
        DeviceCommand.objects.filter(
            device=device,
            command=verb,
            ack_status=DeviceCommand.ACK_PENDING,
        )
        .order_by("-sent_at")
        .first()
    )
    if pending is None:
        return False
    DeviceCommand.objects.filter(pk=pending.pk).update(
        ack_status=_resolve_ack_status(status),
        ack_at=timezone.now(),
        ack_payload=ack_payload or None,
    )
    return True


__all__ = [
    "DeviceCommandError",
    "publish_command",
    "dispatch_command",
    "apply_command_ack",
    "apply_command_ack_by_verb",
]
