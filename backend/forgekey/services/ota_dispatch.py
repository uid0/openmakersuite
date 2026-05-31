"""
OTA firmware-trigger dispatch.

Layered on top of ``firmware_download_token`` (signed URL minting) and
``device_commands``-style MQTT publish, this module is the single entry
point used by the ``trigger_ota`` celery task and the admin
"Deploy to fleet" action.

Compared to the older ``firmware_dispatch.publish_firmware_update`` flow
(which drops a retained payload on ``forgekey/<mac>/firmware`` advertising
the latest available image), the OTA flow is *imperative*: the server tells
a specific device to download a specific firmware via a one-shot signed
URL, and the device reports back on ``forgekey/<mac>/ota/status``. The two
flows can coexist; new firmware should prefer the OTA flow.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional
from urllib.parse import urljoin

from django.conf import settings
from django.urls import reverse

import paho.mqtt.client as mqtt

from ..audit import record_event as record_audit_event
from ..models import DeviceFirmwareUpdate, ESP32Device, FirmwareVersion
from ..utils import get_mqtt_ota_trigger_topic
from .firmware_download_token import DEFAULT_TTL_SECONDS, make_download_token

logger = logging.getLogger(__name__)


class OTADispatchError(RuntimeError):
    """Raised when the broker rejects an OTA trigger publish."""


def _firmware_size(firmware: FirmwareVersion) -> Optional[int]:
    if not firmware.firmware_file:
        return None
    try:
        return int(firmware.firmware_file.size)
    except (OSError, ValueError):
        return None


def _public_base_url() -> str:
    """Best-effort absolute URL prefix for the OTA download endpoint.

    Uses ``FORGEKEY_PUBLIC_BASE_URL`` if set; otherwise returns an empty
    string and callers fall back to a path-only URL (devices on the same
    LAN as the broker can typically resolve the OMS host themselves).
    """
    base = getattr(settings, "FORGEKEY_PUBLIC_BASE_URL", "") or ""
    return base.rstrip("/")


def build_download_url(
    firmware: FirmwareVersion, *, ttl_seconds: int = DEFAULT_TTL_SECONDS
) -> Dict[str, Any]:
    """Mint a signed URL for the firmware binary.

    Returns a dict with ``url``, ``token``, and ``exp`` so callers can log
    the components separately if they want. The path is built off the
    ``forgekey:firmware-download`` URL name so it tracks any future routing
    change.
    """
    token, expiry = make_download_token(str(firmware.id), ttl_seconds=ttl_seconds)
    path = reverse("forgekey:firmware-download", kwargs={"firmware_id": str(firmware.id)})
    base = _public_base_url()
    relative = f"{path}?token={token}&exp={expiry}"
    if base:
        url = urljoin(base + "/", relative.lstrip("/"))
    else:
        url = relative
    return {"url": url, "token": token, "exp": expiry, "path": path}


def build_ota_payload(
    firmware: FirmwareVersion, *, ttl_seconds: int = DEFAULT_TTL_SECONDS
) -> Dict[str, Any]:
    """Build the JSON payload published to ``forgekey/<mac>/ota/trigger``.

    Schema (per oms-k2f / fo-e68):
      ``{"version": "...", "url": "https://...", "signature": "<base64>", "size": <int>}``

    Additional fields included for the firmware's convenience:
      ``sha256`` — hex digest, lets the device verify the binary fetch
      ``mandatory`` — already on FirmwareVersion
      ``firmware_id`` — UUID, echoed back in OTA status reports
    """
    minted = build_download_url(firmware, ttl_seconds=ttl_seconds)
    payload: Dict[str, Any] = {
        "version": firmware.version,
        "url": minted["url"],
        "signature": firmware.signature or "",
        "size": _firmware_size(firmware) or 0,
        "sha256": firmware.sha256 or "",
        "mandatory": bool(firmware.mandatory),
        "firmware_id": str(firmware.id),
    }
    return payload


def publish_ota_trigger(
    device: ESP32Device,
    firmware: FirmwareVersion,
    *,
    requested_by=None,
    client: Optional[mqtt.Client] = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    rollout=None,
) -> DeviceFirmwareUpdate:
    """Publish an OTA trigger for a single device and record an update row.

    Returns the created :class:`DeviceFirmwareUpdate`. Raises
    :class:`OTADispatchError` on broker rejection — the row is still
    written so an operator can re-run dispatch without losing the audit
    trail.
    """
    # Imported lazily to keep import-time cost low and avoid a circular
    # import with forgekey.tasks (which imports ota_dispatch on demand).
    from ..tasks import get_mqtt_client

    payload = build_ota_payload(firmware, ttl_seconds=ttl_seconds)
    payload["update_id"] = None  # filled in below once the row exists

    record = DeviceFirmwareUpdate.objects.create(
        device=device,
        firmware_version=firmware,
        status=DeviceFirmwareUpdate.STATUS_PENDING,
        requested_by=requested_by,
        rollout=rollout,
    )
    record_audit_event(
        action="firmware_request",
        actor=requested_by,
        firmware_update=record,
        device=device,
        metadata={
            "firmware_version": firmware.version,
            "dispatch_path": "ota",
        },
    )
    payload["update_id"] = str(record.id)

    topic = get_mqtt_ota_trigger_topic(device.mac_address)
    body = json.dumps(payload)
    broker = client or get_mqtt_client()
    try:
        result = broker.publish(topic, body, qos=1)
    except Exception as exc:  # pragma: no cover - logged for ops
        logger.exception(
            "OTA trigger publish raised for device %s on %s",
            device.mac_address,
            topic,
        )
        raise OTADispatchError(f"MQTT publish raised: {exc}") from exc

    rc = getattr(result, "rc", 0)
    if rc != 0:
        logger.error(
            "OTA trigger publish failed: device=%s topic=%s rc=%s firmware=%s",
            device.mac_address,
            topic,
            rc,
            firmware.version,
        )
        raise OTADispatchError(f"MQTT publish failed (rc={rc})")

    logger.info(
        "OTA trigger published: device=%s mac=%s firmware=%s update_id=%s",
        device.id,
        device.mac_address,
        firmware.version,
        record.id,
    )
    return record


__all__ = (
    "OTADispatchError",
    "build_download_url",
    "build_ota_payload",
    "publish_ota_trigger",
)
