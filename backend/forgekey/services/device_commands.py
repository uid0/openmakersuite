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


def publish_command(
    device: ESP32Device,
    command_payload: Dict[str, Any],
    *,
    actor: Optional[Any] = None,
    audit_action: Optional[str] = None,
    client: Optional[mqtt.Client] = None,
) -> str:
    """Publish a JSON command payload to a device's MQTT command topic.

    Returns the topic the message was published on. Raises
    :class:`DeviceCommandError` on broker rejection so callers can map it to
    an HTTP 502 / retryable failure.
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
    normalized = (status or "").lower()
    if normalized in ("ok", "success", "acked", "complete", "completed", "done"):
        ack_status = DeviceCommand.ACK_OK
    elif normalized in ("error", "failed", "failure", "rejected", "unknown_command"):
        ack_status = DeviceCommand.ACK_ERROR
    else:
        # Treat anything else as an error so the UI surfaces the firmware
        # message rather than silently leaving the command pending.
        ack_status = DeviceCommand.ACK_ERROR

    rows = DeviceCommand.objects.filter(id=command_id, ack_status=DeviceCommand.ACK_PENDING).update(
        ack_status=ack_status,
        ack_at=timezone.now(),
        ack_payload=ack_payload or None,
    )
    return rows > 0


__all__ = ["DeviceCommandError", "publish_command", "dispatch_command", "apply_command_ack"]
