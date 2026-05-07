"""
MQTT firmware-update dispatch.

The ESP32 firmware on each ForgeKey device subscribes to
``forgekey/<mac>/firmware`` and applies any new release advertised there.
``publish_firmware_update`` is the single entry point for both single-device
and bulk rollouts; it records a ``DeviceFirmwareUpdate`` row per dispatched
device so we have a durable audit trail independent of the broker.
"""

from __future__ import annotations

import json
import logging
from typing import Iterable, List, Sequence, Union

from django.db.models import QuerySet

from ..audit import record_event as record_audit_event
from ..models import DeviceFirmwareUpdate, ESP32Device, FirmwareVersion
from ..tasks import get_mqtt_client
from ..utils import get_mqtt_firmware_topic, normalize_mac_address

logger = logging.getLogger(__name__)


DeviceArg = Union[ESP32Device, Iterable[ESP32Device], "QuerySet[ESP32Device]"]


def _coerce_devices(devices: DeviceArg) -> List[ESP32Device]:
    if isinstance(devices, ESP32Device):
        return [devices]
    return list(devices)


def _build_payload(firmware: FirmwareVersion) -> dict:
    return {
        "url": firmware.effective_binary_url,
        "sha256": firmware.sha256,
        "signature": firmware.signature or "",
        "version": firmware.version,
        "mandatory": firmware.mandatory,
    }


def publish_firmware_update(
    devices: DeviceArg,
    firmware: FirmwareVersion,
    *,
    requested_by=None,
) -> List[DeviceFirmwareUpdate]:
    """
    Publish a firmware-update advertisement for each device and record a
    pending ``DeviceFirmwareUpdate`` row. Returns the created rows.

    The MQTT publish is best-effort: if the broker rejects a single message
    the corresponding update row is still recorded so an operator can re-run
    the dispatch. Broker-level failures bubble up only if no devices succeed.
    """

    device_list = _coerce_devices(devices)
    if not device_list:
        return []

    payload = _build_payload(firmware)
    payload_json = json.dumps(payload)

    client = get_mqtt_client()
    records: List[DeviceFirmwareUpdate] = []
    publish_failures = 0

    for device in device_list:
        topic = get_mqtt_firmware_topic(device.mac_address)
        try:
            result = client.publish(topic, payload_json, qos=1, retain=True)
            rc = getattr(result, "rc", 0)
            if rc != 0:
                publish_failures += 1
                logger.error(
                    "MQTT publish failed for device %s on topic %s (rc=%s)",
                    device.mac_address,
                    topic,
                    rc,
                )
        except Exception:  # pragma: no cover - logged for ops
            publish_failures += 1
            logger.exception(
                "MQTT publish raised for device %s on topic %s",
                device.mac_address,
                topic,
            )

        record = DeviceFirmwareUpdate.objects.create(
            device=device,
            firmware_version=firmware,
            status=DeviceFirmwareUpdate.STATUS_PENDING,
            requested_by=requested_by,
        )
        record_audit_event(
            action="firmware_request",
            actor=requested_by,
            firmware_update=record,
            device=device,
            metadata={
                "firmware_version": firmware.version,
                "dispatch_path": "mqtt",
            },
        )
        records.append(record)

    if publish_failures and publish_failures == len(device_list):
        raise RuntimeError(
            f"All {publish_failures} MQTT firmware publishes failed for " f"{firmware.version}"
        )

    return records


def dispatch_to_device_type(
    firmware: FirmwareVersion,
    *,
    device_type_code: str | None = None,
    requested_by=None,
) -> List[DeviceFirmwareUpdate]:
    """
    Bulk dispatch helper — selects every active device matching the firmware's
    device type (or an explicit override) and publishes to each.
    """

    code = device_type_code or firmware.device_type.code
    queryset: "QuerySet[ESP32Device]" = ESP32Device.objects.filter(
        device_type__code=code,
        is_active=True,
    )
    return publish_firmware_update(queryset, firmware, requested_by=requested_by)


def normalize_topic_for(mac_address: str) -> str:
    """Convenience wrapper for tests / external callers."""
    return get_mqtt_firmware_topic(normalize_mac_address(mac_address))


__all__: Sequence[str] = (
    "publish_firmware_update",
    "dispatch_to_device_type",
    "normalize_topic_for",
)
