"""
Long-running MQTT subscriber for ForgeKey devices.

Subscribes to two wildcard topics:

* ``forgekey/+/+/occupancy`` — per-event occupancy messages from people
  counters and door counters. Persisted as :class:`OccupancyEvent` rows.
* ``forgekey/+/status`` — periodic status / boot messages from any device.
  Updates :class:`ESP32Device.last_seen` and related fields in place.

The command is intended to be run under a process supervisor (systemd unit
or docker-compose service). It handles broker disconnects, malformed
payloads, and unknown MAC addresses without crashing the consumer loop.
"""

from __future__ import annotations

import json
import logging
import signal
import ssl
import time
from datetime import datetime, timezone
from typing import Any, Optional

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone as dj_timezone

import paho.mqtt.client as mqtt

from forgekey.models import ESP32Device, OccupancyEvent
from forgekey.utils import normalize_mac_address, normalize_sensor_kind

logger = logging.getLogger(__name__)


def _mac_from_topic_segment(segment: str) -> Optional[str]:
    """Reverse the firmware contract MAC encoding (lowercase, no separators).

    Returns the colon-form MAC, or ``None`` if the segment isn't 12 hex chars.
    """
    if len(segment) != 12:
        return None
    try:
        int(segment, 16)
    except ValueError:
        return None
    return normalize_mac_address(segment)


def _parse_event_timestamp(raw: Any) -> datetime:
    """Best-effort parse of a device-supplied timestamp.

    Accepts ISO-8601 strings (with or without trailing ``Z``) or epoch seconds.
    Falls back to current server time on anything unparseable.
    """
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return dj_timezone.now()
    if isinstance(raw, str) and raw:
        candidate = raw.strip()
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return dj_timezone.now()
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    return dj_timezone.now()


def handle_occupancy_message(topic: str, payload: bytes) -> Optional[OccupancyEvent]:
    """Parse and persist an occupancy event. Returns the row, or ``None`` on drop.

    Drops messages that fail validation rather than raising, so the broker
    callback can keep the loop alive.
    """
    parts = topic.split("/")
    if len(parts) < 4:
        logger.warning("Dropping occupancy message: malformed topic %r", topic)
        return None

    mac = _mac_from_topic_segment(parts[1])
    if mac is None:
        logger.warning("Dropping occupancy message: bad MAC segment %r in %r", parts[1], topic)
        return None

    sensor_kind = normalize_sensor_kind(parts[2])

    try:
        body = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("Dropping occupancy message on %s: invalid JSON (%s)", topic, exc)
        return None
    if not isinstance(body, dict):
        logger.warning("Dropping occupancy message on %s: payload is not an object", topic)
        return None

    try:
        device = ESP32Device.objects.get(mac_address=mac)
    except ESP32Device.DoesNotExist:
        logger.info("Dropping occupancy message: unknown MAC %s on topic %s", mac, topic)
        return None

    count_in_raw = body.get("in", 0)
    count_out_raw = body.get("out", 0)
    try:
        count_in = max(int(count_in_raw), 0)
        count_out = max(int(count_out_raw), 0)
    except (TypeError, ValueError):
        logger.warning(
            "Dropping occupancy message on %s: non-integer in/out values %r/%r",
            topic,
            count_in_raw,
            count_out_raw,
        )
        return None

    event_ts = _parse_event_timestamp(body.get("ts"))

    event = OccupancyEvent.objects.create(
        device=device,
        sensor_kind=sensor_kind,
        count_in=count_in,
        count_out=count_out,
        event_timestamp_utc=event_ts,
        raw_payload=body,
    )
    # Touch last_seen on every observed event so the dashboards reflect that
    # the device is publishing, not just that it last booted.
    ESP32Device.objects.filter(pk=device.pk).update(
        last_seen=dj_timezone.now(),
        is_online=True,
    )
    return event


def handle_status_message(topic: str, payload: bytes) -> bool:
    """Update device last_seen / status fields from a status message.

    Returns ``True`` if a device row was updated, ``False`` otherwise.
    """
    parts = topic.split("/")
    if len(parts) < 3:
        logger.warning("Dropping status message: malformed topic %r", topic)
        return False
    mac = _mac_from_topic_segment(parts[1])
    if mac is None:
        logger.warning("Dropping status message: bad MAC segment %r in %r", parts[1], topic)
        return False

    body: dict = {}
    if payload:
        try:
            decoded = json.loads(payload.decode("utf-8"))
            if isinstance(decoded, dict):
                body = decoded
        except (UnicodeDecodeError, json.JSONDecodeError):
            # Status messages may legitimately be empty or non-JSON pings;
            # treat as a heartbeat without per-field updates.
            body = {}

    updates: dict = {
        "last_seen": dj_timezone.now(),
        "is_online": bool(body.get("online", True)),
    }
    if isinstance(body.get("firmware_version"), str):
        updates["firmware_version"] = body["firmware_version"]
    if isinstance(body.get("boot_count"), int):
        updates["boot_count"] = body["boot_count"]
    if isinstance(body.get("free_heap"), int):
        updates["free_heap"] = body["free_heap"]
    if isinstance(body.get("ip"), str):
        updates["ip"] = body["ip"]

    rows = ESP32Device.objects.filter(mac_address=mac).update(**updates)
    if rows == 0:
        logger.info("Dropping status message: unknown MAC %s on topic %s", mac, topic)
        return False
    return True


def _topic_matches_occupancy(topic: str) -> bool:
    parts = topic.split("/")
    return len(parts) == 4 and parts[0] == settings.MQTT_TOPIC_PREFIX and parts[3] == "occupancy"


def _topic_matches_status(topic: str) -> bool:
    parts = topic.split("/")
    return len(parts) == 3 and parts[0] == settings.MQTT_TOPIC_PREFIX and parts[2] == "status"


def dispatch_message(topic: str, payload: bytes) -> None:
    """Route an inbound MQTT message to the appropriate handler.

    Catches handler exceptions so a single bad message can never crash the
    network loop. Used by the paho ``on_message`` callback and by tests.
    """
    try:
        if _topic_matches_occupancy(topic):
            handle_occupancy_message(topic, payload)
        elif _topic_matches_status(topic):
            handle_status_message(topic, payload)
        else:
            logger.debug("Ignoring message on unsubscribed topic %s", topic)
    except Exception:
        logger.exception("Unhandled error while processing MQTT message on %s", topic)


class Command(BaseCommand):
    """Run the ForgeKey MQTT subscriber in the foreground."""

    help = "Subscribe to ForgeKey MQTT topics and persist occupancy + status events."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Connect, run one network iteration, and exit. Used by tests.",
        )

    def handle(self, *args, **options):
        prefix = settings.MQTT_TOPIC_PREFIX
        host = settings.MQTT_BROKER_HOST
        port = settings.MQTT_BROKER_PORT
        client_id = f"{settings.MQTT_CLIENT_ID}-consumer"

        client = mqtt.Client(
            client_id=client_id,
            protocol=mqtt.MQTTv5,
        )

        if settings.MQTT_BROKER_USERNAME:
            client.username_pw_set(
                settings.MQTT_BROKER_USERNAME,
                settings.MQTT_BROKER_PASSWORD,
            )

        if getattr(settings, "MQTT_BROKER_TLS", False):
            client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

        occupancy_filter = f"{prefix}/+/+/occupancy"
        status_filter = f"{prefix}/+/status"

        def on_connect(c, userdata, flags, rc, properties=None):
            if rc != 0:
                logger.error("MQTT consumer connect failed rc=%s", rc)
                return
            logger.info(
                "MQTT consumer connected to %s:%s; subscribing to %s and %s",
                host,
                port,
                occupancy_filter,
                status_filter,
            )
            c.subscribe([(occupancy_filter, 1), (status_filter, 1)])

        def on_disconnect(c, userdata, rc, properties=None):
            if rc != 0:
                logger.warning("MQTT consumer disconnected rc=%s; loop will reconnect", rc)

        def on_message(c, userdata, msg):
            dispatch_message(msg.topic, msg.payload)

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message

        stop_requested = {"flag": False}

        def _request_stop(signum, frame):
            logger.info("MQTT consumer received signal %s; shutting down", signum)
            stop_requested["flag"] = True
            try:
                client.disconnect()
            except Exception:  # pragma: no cover - best effort during shutdown
                logger.exception("Error during MQTT client disconnect")

        signal.signal(signal.SIGTERM, _request_stop)
        signal.signal(signal.SIGINT, _request_stop)

        # Initial connect — paho's loop handles reconnects after the first
        # successful socket open. If the very first attempt fails (broker not
        # up yet), retry with backoff so a supervisor restart isn't required.
        backoff = 1.0
        while not stop_requested["flag"]:
            try:
                client.connect(host, port, settings.MQTT_KEEPALIVE)
                break
            except Exception as exc:
                logger.error("MQTT consumer initial connect failed: %s", exc)
                if options.get("once"):
                    return
                time.sleep(min(backoff, 30.0))
                backoff = min(backoff * 2, 30.0)

        if options.get("once"):
            client.loop(timeout=1.0)
            client.disconnect()
            return

        client.loop_forever(retry_first_connection=True)
