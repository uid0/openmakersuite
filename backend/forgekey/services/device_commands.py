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
from typing import Any, Dict, Optional

from django.utils import timezone

import paho.mqtt.client as mqtt

from ..models import ESP32Device
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


__all__ = ["DeviceCommandError", "publish_command"]
