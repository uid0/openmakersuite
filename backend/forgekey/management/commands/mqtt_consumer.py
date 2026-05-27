"""
Long-running MQTT subscriber for ForgeKey devices.

Subscribes to four wildcard topics:

* ``forgekey/+/+/occupancy`` — per-event occupancy messages from people
  counters and door counters. Persisted as :class:`OccupancyEvent` rows.
* ``forgekey/+/status`` — periodic status / boot messages from any device.
  Updates :class:`ESP32Device.last_seen` and related fields in place.
* ``forgekey/+/ota/status`` — OTA progress reports against an in-flight
  :class:`DeviceFirmwareUpdate`.
* ``forgekey/+/logs`` — structured log lines from device firmware
  (``MqttClient::publishLog``). Level ≥ WARNING forwards to Sentry with
  device tags; lower levels are recorded as Django log breadcrumbs only.

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
from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.utils import timezone as dj_timezone

import paho.mqtt.client as mqtt
import sentry_sdk

from config.observability_redaction import redact
from forgekey.models import DeviceFirmwareUpdate, ESP32Device, OccupancyEvent
from forgekey.services.jwt_signing import JwtSigningError, is_jwt_signing_configured
from forgekey.utils import (
    SERVER_JWT_SUBJECT,
    generate_server_jwt,
    normalize_mac_address,
    normalize_sensor_kind,
)

logger = logging.getLogger(__name__)

# Per-device rate cap for log forwarding so a wedged device can't fill the
# Sentry project. 60 forwarded events per 5 minutes per device covers a
# normal error storm while bounding the worst case. Drops past the cap
# bump a Sentry-side counter so the misbehaving device is still visible.
LOG_RATE_LIMIT_MAX_EVENTS = 60
LOG_RATE_LIMIT_WINDOW_SECONDS = 300

# Firmware-supplied log level strings → Python logging level ints + the
# Sentry level wire format. Anything not in this map is treated as INFO.
_LOG_LEVELS: dict[str, tuple[int, str]] = {
    "trace": (logging.DEBUG, "debug"),
    "debug": (logging.DEBUG, "debug"),
    "info": (logging.INFO, "info"),
    "warn": (logging.WARNING, "warning"),
    "warning": (logging.WARNING, "warning"),
    "error": (logging.ERROR, "error"),
    "fatal": (logging.CRITICAL, "fatal"),
    "crit": (logging.CRITICAL, "fatal"),
    "critical": (logging.CRITICAL, "fatal"),
}


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
        # gh #378: scrub the raw payload before persisting. Devices
        # occasionally publish unexpected fields (debug auth headers,
        # provisioning tokens reflected back from a misbehaving firmware
        # build); redact strips secret-shaped values without dropping the
        # operator-useful counts that the schema expects.
        raw_payload=redact(body),
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

    # Firmware ack channel: a status payload may include ``cmd_ack`` to
    # mark a :class:`DeviceCommand` row as acked. Two shapes are accepted
    # so we work with both the current firmware (flat) and any future
    # firmware that grows the richer object form:
    #
    #   * Object form (preferred):
    #       ``cmd_ack: {"command_id": "<uuid>", "status": "ok|error", ...}``
    #     Acks a specific row by id — robust under burst commands.
    #
    #   * Flat form (current firmware):
    #       ``cmd_ack: "<verb>"`` with sibling ``command_id`` / ``status``
    #     fields, OR just the verb. Falls back to the most-recent
    #     ``ACK_PENDING`` row of that verb for this device — best-effort
    #     when the firmware doesn't echo the id.
    #
    # Lives in status (not a separate topic) so the firmware needs no
    # new MQTT subscription.
    if isinstance(body, dict) and "cmd_ack" in body:
        from forgekey.services.device_commands import apply_command_ack, apply_command_ack_by_verb

        ack = body.get("cmd_ack")
        sibling_command_id = body.get("command_id")
        sibling_status = body.get("status") or body.get("ack_status") or "ok"

        if isinstance(ack, dict):
            apply_command_ack(
                command_id=str(ack.get("command_id") or sibling_command_id or ""),
                status=str(ack.get("status") or sibling_status or ""),
                ack_payload=ack,
            )
        elif isinstance(ack, str):
            verb = ack.strip()
            if sibling_command_id:
                apply_command_ack(
                    command_id=str(sibling_command_id),
                    status=str(sibling_status),
                    ack_payload={
                        "cmd_ack": verb,
                        **{k: v for k, v in body.items() if k != "cmd_ack"},
                    },
                )
            elif verb:
                apply_command_ack_by_verb(
                    device=ESP32Device.objects.filter(mac_address=mac).first(),
                    verb=verb,
                    status=str(sibling_status),
                    ack_payload={
                        "cmd_ack": verb,
                        **{k: v for k, v in body.items() if k != "cmd_ack"},
                    },
                )
    return True


def handle_ota_status_message(topic: str, payload: bytes) -> bool:
    """Apply an inbound OTA status report to the matching DeviceFirmwareUpdate.

    Expected payload shape (firmware contract — fo-e68):
      ``{"update_id": "<uuid>", "status": "in_progress|completed|failed",
         "error_message": "<optional>", "version": "<optional>"}``

    Returns ``True`` if a row was updated, ``False`` if dropped.
    """
    parts = topic.split("/")
    if len(parts) < 4:
        logger.warning("Dropping OTA status message: malformed topic %r", topic)
        return False
    mac = _mac_from_topic_segment(parts[1])
    if mac is None:
        logger.warning("Dropping OTA status message: bad MAC segment %r in %r", parts[1], topic)
        return False

    try:
        body = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("Dropping OTA status on %s: invalid JSON (%s)", topic, exc)
        return False
    if not isinstance(body, dict):
        logger.warning("Dropping OTA status on %s: payload is not an object", topic)
        return False

    update_id = body.get("update_id")
    status_value = (body.get("status") or "").lower()
    if not update_id or not status_value:
        logger.warning("Dropping OTA status on %s: missing update_id/status", topic)
        return False

    try:
        device = ESP32Device.objects.get(mac_address=mac)
    except ESP32Device.DoesNotExist:
        logger.info("Dropping OTA status: unknown MAC %s on topic %s", mac, topic)
        return False

    try:
        update = DeviceFirmwareUpdate.objects.select_related("firmware_version").get(
            id=update_id, device=device
        )
    except DeviceFirmwareUpdate.DoesNotExist:
        logger.info(
            "Dropping OTA status: no DeviceFirmwareUpdate for device=%s update_id=%s",
            mac,
            update_id,
        )
        return False

    error_message = body.get("error_message") or body.get("error") or ""
    progress = body.get("progress")

    if status_value in ("started", "in_progress", "downloading", "applying"):
        update.status = DeviceFirmwareUpdate.STATUS_IN_PROGRESS
        if update.started_at is None:
            update.started_at = dj_timezone.now()
    elif status_value in ("completed", "success", "applied"):
        update.status = DeviceFirmwareUpdate.STATUS_COMPLETED
        update.completed_at = dj_timezone.now()
        applied_version = body.get("version") or update.firmware_version.version
        if isinstance(applied_version, str) and applied_version:
            ESP32Device.objects.filter(pk=device.pk).update(firmware_version=applied_version)
    elif status_value in ("failed", "error", "rejected"):
        update.status = DeviceFirmwareUpdate.STATUS_FAILED
        update.completed_at = dj_timezone.now()
        if error_message:
            # gh #378: device-supplied error text occasionally includes the
            # signing-key fingerprint that was rejected, or a quoted auth
            # header. Redact before truncation so the persisted value
            # never reproduces the credential.
            update.error_message = redact(str(error_message))[:2000]
    elif status_value in ("cancelled", "canceled"):
        update.status = DeviceFirmwareUpdate.STATUS_CANCELLED
        update.completed_at = dj_timezone.now()
    else:
        logger.info(
            "OTA status %r on %s for update_id=%s — no state change",
            status_value,
            topic,
            update_id,
        )
        return False

    update.save()
    if progress is not None:
        logger.info(
            "OTA progress device=%s update=%s status=%s progress=%s",
            mac,
            update.id,
            status_value,
            progress,
        )
    return True


def _topic_matches_occupancy(topic: str) -> bool:
    parts = topic.split("/")
    return len(parts) == 4 and parts[0] == settings.MQTT_TOPIC_PREFIX and parts[3] == "occupancy"


def _topic_matches_status(topic: str) -> bool:
    parts = topic.split("/")
    return len(parts) == 3 and parts[0] == settings.MQTT_TOPIC_PREFIX and parts[2] == "status"


def _topic_matches_ota_status(topic: str) -> bool:
    parts = topic.split("/")
    return (
        len(parts) == 4
        and parts[0] == settings.MQTT_TOPIC_PREFIX
        and parts[2] == "ota"
        and parts[3] == "status"
    )


def _topic_matches_log(topic: str) -> bool:
    parts = topic.split("/")
    return len(parts) == 3 and parts[0] == settings.MQTT_TOPIC_PREFIX and parts[2] == "logs"


def _device_log_allowed(mac: str) -> bool:
    """Sliding-window rate limiter for per-device log forwarding to Sentry.

    Uses the Django cache (django-redis in prod) so the budget is shared
    across the cluster. Returns True when this event is within budget;
    False when it should be dropped.
    """
    bucket_key = f"forgekey:log_rate:{mac}"
    try:
        # Seed the key (no-op if already set) so incr has something to bump.
        # get_or_set + incr is atomic enough for our purposes on the redis
        # backend; we don't need exact accuracy, just a hard cap.
        cache.get_or_set(bucket_key, 0, timeout=LOG_RATE_LIMIT_WINDOW_SECONDS)
        next_value = cache.incr(bucket_key)
    except ValueError:
        # Key expired between get_or_set and incr — start over.
        cache.set(bucket_key, 1, timeout=LOG_RATE_LIMIT_WINDOW_SECONDS)
        next_value = 1
    except Exception:
        # Cache backend wedged — fail open so a redis outage doesn't drop
        # all device logs. Logged once per call so we notice in Sentry.
        logger.exception("Rate-limit cache backend unavailable; fail-open for %s", mac)
        return True
    return next_value <= LOG_RATE_LIMIT_MAX_EVENTS


def handle_log_message(topic: str, payload: bytes) -> bool:
    """Forward a device log line to Sentry (level ≥ WARNING) and Django logs.

    Firmware contract: ``MqttClient::publishLog(timestampMs, level, tag, message)``
    publishes ``{"ts": <ms>, "level": "<info|warning|error|...>",
    "tag": "<subsystem>", "msg": "<text>"}`` to
    ``forgekey/<mac>/logs``. We:

    * drop the message when MAC isn't registered (don't trust unknown devices),
    * scrub ``msg`` + ``tag`` through ``observability_redaction.redact`` so a
      device echoing a JWT or PEM never reaches Sentry verbatim,
    * rate-limit per device to bound the worst case (wedged firmware loop),
    * call ``sentry_sdk.capture_message`` for warning/error/fatal — lower
      levels become Django log records only (Sentry's LoggingIntegration
      turns those into breadcrumbs on the next captured event).

    Returns ``True`` if forwarded to Sentry, ``False`` otherwise.
    """
    parts = topic.split("/")
    if len(parts) != 3:
        logger.warning("Dropping log message: malformed topic %r", topic)
        return False
    mac = _mac_from_topic_segment(parts[1])
    if mac is None:
        logger.warning("Dropping log message: bad MAC segment %r in %r", parts[1], topic)
        return False

    try:
        body = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("Dropping log message on %s: invalid JSON (%s)", topic, exc)
        return False
    if not isinstance(body, dict):
        logger.warning("Dropping log message on %s: payload is not an object", topic)
        return False

    device = (
        ESP32Device.objects.filter(mac_address=mac)
        .only("id", "mac_address", "firmware_version", "location_id")
        .first()
    )
    if device is None:
        # Unregistered MACs: don't trust their input. Provisioning will create
        # the row before any legitimate log traffic.
        logger.info("Dropping log message: unknown MAC %s on topic %s", mac, topic)
        return False

    raw_level = str(body.get("level") or "info").strip().lower()
    py_level, sentry_level = _LOG_LEVELS.get(raw_level, (logging.INFO, "info"))
    raw_tag = body.get("tag")
    tag = redact(raw_tag) if isinstance(raw_tag, str) else None
    raw_msg = body.get("msg") or body.get("message") or ""
    message = redact(raw_msg) if isinstance(raw_msg, str) else str(raw_msg)

    # Always echo to Django logger — LoggingIntegration converts INFO/DEBUG
    # into Sentry breadcrumbs that ride along with the next captured event,
    # so even sub-warning lines stay useful for postmortem.
    logger.log(
        py_level,
        "[device %s/%s] %s",
        device.mac_address,
        tag or "-",
        message,
        extra={"device_id": device.id, "device_mac": device.mac_address, "device_log_tag": tag},
    )

    # Only forward warning+ to Sentry as a discrete event, and only when the
    # device hasn't blown its rate budget for the window.
    if py_level < logging.WARNING:
        return False
    if not _device_log_allowed(device.mac_address):
        # One-line drop record per cap-exceed so a wedged device is still
        # visible (this line itself rides the rate budget so the meta-event
        # doesn't loop forever).
        logger.warning(
            "Rate-limit drop: device %s exceeded %d log forwards / %ds",
            device.mac_address,
            LOG_RATE_LIMIT_MAX_EVENTS,
            LOG_RATE_LIMIT_WINDOW_SECONDS,
        )
        return False

    # Tag the event on a fresh scope so device context doesn't leak into
    # unrelated requests on the same thread (sentry-sdk 2.x scope model).
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("origin", "device")
        scope.set_tag("device_id", str(device.id))
        scope.set_tag("device_mac", device.mac_address)
        if device.firmware_version:
            scope.set_tag("device_firmware", device.firmware_version)
        if device.location_id is not None:
            scope.set_tag("device_location", str(device.location_id))
        if tag:
            scope.set_tag("device_log_tag", tag)
        sentry_sdk.capture_message(message, level=sentry_level)
    return True


def dispatch_message(topic: str, payload: bytes) -> None:
    """Route an inbound MQTT message to the appropriate handler.

    Catches handler exceptions so a single bad message can never crash the
    network loop. Used by the paho ``on_message`` callback and by tests.
    """
    try:
        if _topic_matches_ota_status(topic):
            handle_ota_status_message(topic, payload)
        elif _topic_matches_occupancy(topic):
            handle_occupancy_message(topic, payload)
        elif _topic_matches_log(topic):
            handle_log_message(topic, payload)
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

        # Prefer the server-JWT pattern (oms-y5p): a self-issued ES256 JWT
        # signed with the device-JWT key. EMQX's JWT authenticator verifies
        # it via the same JWKS endpoint at /api/forgekey/jwks/, so no
        # separate user-management is required. Fall back to literal
        # MQTT_BROKER_USERNAME / MQTT_BROKER_PASSWORD only when JWT signing
        # is unconfigured — exists for dev rigs that haven't enrolled in
        # JWT auth yet. Mirrors `forgekey.tasks.get_mqtt_client` exactly so
        # the consumer and the publisher authenticate identically.
        if is_jwt_signing_configured():
            try:
                client.username_pw_set(SERVER_JWT_SUBJECT, generate_server_jwt())
            except JwtSigningError as exc:
                logger.error("Failed to mint server JWT for MQTT consumer: %s", exc)
                raise
        elif settings.MQTT_BROKER_USERNAME:
            client.username_pw_set(
                settings.MQTT_BROKER_USERNAME,
                settings.MQTT_BROKER_PASSWORD,
            )

        if getattr(settings, "MQTT_BROKER_TLS", False):
            client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

        occupancy_filter = f"{prefix}/+/+/occupancy"
        status_filter = f"{prefix}/+/status"
        ota_status_filter = f"{prefix}/+/ota/status"
        log_filter = f"{prefix}/+/logs"

        def on_connect(c, userdata, flags, rc, properties=None):
            if rc != 0:
                logger.error("MQTT consumer connect failed rc=%s", rc)
                return
            logger.info(
                "MQTT consumer connected to %s:%s; subscribing to %s, %s, %s, %s",
                host,
                port,
                occupancy_filter,
                status_filter,
                ota_status_filter,
                log_filter,
            )
            c.subscribe(
                [
                    (occupancy_filter, 1),
                    (status_filter, 1),
                    (ota_status_filter, 1),
                    (log_filter, 1),
                ]
            )

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
