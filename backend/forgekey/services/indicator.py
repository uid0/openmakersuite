"""Indicator status → presentation policy + MQTT dispatch (epic ga-72l).

OMS owns the *visual policy*: it derives an operational status for the bound
target (asset or room), maps that status to an explicit
``(color, brightness, pattern)`` presentation, and pushes it to the device as a
``set_indicator`` command. Firmware is a dumb renderer — the mapping is tunable
here without reflashing.

Status precedence for assets (first match wins) and the canonical presentation
table both live in epic ga-72l; this module is the single source of truth for
the OMS side of that contract.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from django.utils import timezone

from ..audit import record_event
from ..models import (
    AssetDevice,
    DeviceLockout,
    DeviceUsage,
    ESP32Device,
    ForgeKeyAuditEvent,
    IndicatorBinding,
    IndicatorStatus,
    OperationalMode,
    RoomOperationalMode,
)
from .device_commands import DeviceCommandError, dispatch_command

logger = logging.getLogger(__name__)

# The MQTT command verb the firmware renders (alias of the legacy set_pattern).
INDICATOR_COMMAND = "set_indicator"

# Firmware-compat shim (op-8ph) — Phase 1, no reflash required.
# Current firmware reads the colour NAME from the ``indicator`` field (falling
# back to ``pattern``) and NEVER from ``color``; it also only knows a small fixed
# vocabulary. So a ga-72l payload like {color: green, pattern: solid} renders
# nothing (the device falls back to pattern "solid", which isn't a colour word).
# Until the firmware learns the full contract (Phase 2 / fk-lrd reflash), we ALSO
# emit an ``indicator`` field carrying a firmware-recognised name. ``color`` /
# ``brightness`` / ``pattern`` / ``period_ms`` are kept unchanged for the
# canonical contract and forward firmware.
_FIRMWARE_INDICATOR_FOR_COLOR: Dict[str, str] = {
    "green": "green",
    "red": "red",
    "blue": "blue",
    "yellow": "yellow",
    "purple": "blue",  # firmware has no purple yet — temporary stand-in (Phase 2 adds it)
}


def firmware_indicator(color: Optional[str], pattern: Optional[str]) -> Optional[str]:
    """Map a ga-72l ``(color, pattern)`` to a name current firmware renders.

    Returns the ``indicator`` word to add for firmware-compat, or ``None`` when
    there is nothing to add (the device then uses its own ``pattern`` fallback).
    """
    if color:
        return _FIRMWARE_INDICATOR_FOR_COLOR.get(color, color)
    if pattern == "off":
        return "off"
    return None

# status → explicit presentation. ``None`` color/brightness are omitted from the
# payload (e.g. locked-out sends just ``pattern: off``). period_ms is only sent
# for patterns that animate. This is the authoritative ga-72l mapping.
PRESENTATIONS: Dict[str, Dict[str, Any]] = {
    IndicatorStatus.AVAILABLE: {
        "color": "green",
        "brightness": "low",
        "pattern": "solid",
        "period_ms": None,
    },
    IndicatorStatus.IN_USE: {
        "color": "green",
        "brightness": "high",
        "pattern": "solid",
        "period_ms": None,
    },
    IndicatorStatus.UNAVAILABLE: {
        "color": "red",
        "brightness": "low",
        "pattern": "solid",
        "period_ms": None,
    },
    IndicatorStatus.LOCKED_OUT: {
        "color": None,
        "brightness": None,
        "pattern": "off",
        "period_ms": None,
    },
    IndicatorStatus.CLASSROOM: {
        "color": "purple",
        "brightness": "high",
        "pattern": "slow_blink",
        "period_ms": 1500,
    },
}

# Accepted firmware vocab for the explicit /indicator/test preview path.
VALID_PATTERNS = {"solid", "blink", "slow_blink", "breathe", "off"}
VALID_BRIGHTNESS_WORDS = {"low", "high"}

# Transient "access denied" feedback (op-vj9): a short red blink the firmware
# renders for ``duration_s`` then drops back to its steady status. Unlike a
# status sync this is deliberately ephemeral — it never updates ``last_status``,
# so the bound light returns to whatever the asset's derived status is.
DENY_FLASH_PAYLOAD: Dict[str, Any] = {
    "cmd": INDICATOR_COMMAND,
    "color": "red",
    "indicator": "red",  # firmware-compat (op-8ph): device reads `indicator`, not `color`
    "brightness": "high",
    "pattern": "blink",
    "period_ms": 250,
    "duration_s": 3,
}


# ---------------------------------------------------------------------------
# Presentation mapping
# ---------------------------------------------------------------------------
def presentation_for_status(status: str) -> Dict[str, Any]:
    """Return a copy of the ``{color, brightness, pattern, period_ms}`` mapping.

    Raises ``KeyError`` (via ``ValueError``) for an unknown status so callers
    surface a programming error rather than silently sending nothing.
    """
    try:
        return dict(PRESENTATIONS[status])
    except KeyError as exc:
        raise ValueError(f"Unknown indicator status: {status!r}") from exc


def build_payload(status: str) -> Dict[str, Any]:
    """Build the ``set_indicator`` payload for a derived status.

    Emits fields in the canonical ga-72l order and omits ``color`` /
    ``brightness`` / ``period_ms`` when not applicable to the status.
    """
    presentation = presentation_for_status(status)
    payload: Dict[str, Any] = {"cmd": INDICATOR_COMMAND}
    if presentation["color"] is not None:
        payload["color"] = presentation["color"]
    if presentation["brightness"] is not None:
        payload["brightness"] = presentation["brightness"]
    payload["pattern"] = presentation["pattern"]
    if presentation["period_ms"] is not None:
        payload["period_ms"] = presentation["period_ms"]
    indicator = firmware_indicator(presentation["color"], presentation["pattern"])
    if indicator is not None:
        payload["indicator"] = indicator
    return payload


# ---------------------------------------------------------------------------
# Status derivation
# ---------------------------------------------------------------------------
def _has_offline_primary_device(asset) -> bool:
    """True if the asset's primary control device is currently offline."""
    return AssetDevice.objects.filter(
        asset=asset, is_primary=True, device__is_online=False
    ).exists()


def derive_asset_status(asset) -> str:
    """Derive an asset's operational status by ga-72l precedence (first wins):

    1. locked out — ``OperationalMode.mode == locked_out`` OR active DeviceLockout
    2. in class  — ``OperationalMode.mode == classroom``
    3. in use    — an open ``DeviceUsage`` (``ended_at IS NULL``)
    4. unavailable — maintenance mode, ``asset.status != ACTIVE``, or the primary
       bound device offline
    5. available — default
    """
    mode = OperationalMode.objects.filter(asset=asset).only("mode").first()
    mode_value = mode.mode if mode is not None else None

    if (
        mode_value == OperationalMode.MODE_LOCKED_OUT
        or DeviceLockout.objects.filter(asset=asset, is_active=True).exists()
    ):
        return IndicatorStatus.LOCKED_OUT

    if mode_value == OperationalMode.MODE_CLASSROOM:
        return IndicatorStatus.CLASSROOM

    if DeviceUsage.objects.filter(asset=asset, ended_at__isnull=True).exists():
        return IndicatorStatus.IN_USE

    if (
        mode_value == OperationalMode.MODE_MAINTENANCE
        or asset.status != asset.ACTIVE
        or _has_offline_primary_device(asset)
    ):
        return IndicatorStatus.UNAVAILABLE

    return IndicatorStatus.AVAILABLE


def derive_room_status(location) -> str:
    """Map a room's admin-set ``RoomOperationalMode.mode`` straight to a status.

    Rooms have no derived state; an unset room defaults to available.
    """
    mode = RoomOperationalMode.objects.filter(location=location).only("mode").first()
    if mode is None:
        return IndicatorStatus.AVAILABLE
    return mode.mode


def status_for_binding(binding: IndicatorBinding) -> str:
    """Derive the current status for whichever target the binding points at."""
    if binding.asset_id:
        return derive_asset_status(binding.asset)
    return derive_room_status(binding.location)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
def sync_indicator(binding: IndicatorBinding, *, actor=None, force: bool = False):
    """Recompute the binding's status and push it to the bound device.

    Debounced: returns ``None`` without dispatching when the freshly-derived
    presentation matches what was last pushed (unless ``force``). On dispatch it
    persists the new ``last_*`` snapshot and records an audit event. Returns the
    created ``DeviceCommand`` (or ``None`` on a no-op).
    """
    status = status_for_binding(binding)
    payload = build_payload(status)

    if not force and binding.last_status == status and binding.last_presentation == payload:
        return None

    _topic, record = dispatch_command(
        binding.device, payload, actor=actor, audit_action=INDICATOR_COMMAND
    )

    binding.last_status = status
    binding.last_presentation = payload
    binding.last_synced_at = timezone.now()
    binding.save(update_fields=["last_status", "last_presentation", "last_synced_at", "updated_at"])

    record_event(
        action=ForgeKeyAuditEvent.ACTION_INDICATOR_SYNC,
        actor=actor,
        device=binding.device,
        notes=f"status={status}",
        metadata={"status": status, "presentation": payload},
    )
    return record


def send_indicator_test(
    device: ESP32Device,
    *,
    color: Any = None,
    brightness: Any = None,
    pattern: Optional[str] = None,
    period_ms: Any = None,
    duration_s: Any = None,
    actor=None,
) -> Tuple[Any, Dict[str, Any]]:
    """Validate + dispatch an explicit indicator preview to a device.

    Raises ``ValueError`` on invalid input (callers map to HTTP 400) and
    ``DeviceCommandError`` on broker failure (callers map to 502). Returns the
    created ``DeviceCommand`` and the payload that was sent.
    """
    payload = build_test_payload(
        color=color,
        brightness=brightness,
        pattern=pattern,
        period_ms=period_ms,
        duration_s=duration_s,
    )
    _topic, record = dispatch_command(device, payload, actor=actor, audit_action=INDICATOR_COMMAND)
    record_event(
        action=ForgeKeyAuditEvent.ACTION_INDICATOR_TEST,
        actor=actor,
        device=device,
        notes="manual indicator test",
        metadata={"presentation": payload},
    )
    return record, payload


# ---------------------------------------------------------------------------
# Reactive fan-out helpers (signals + MQTT consumer call these)
# ---------------------------------------------------------------------------
def _bindings_qs():
    return IndicatorBinding.objects.select_related(
        "device", "device__device_type", "asset", "location"
    )


def _safe_sync(binding: IndicatorBinding, *, actor=None) -> None:
    """Sync a binding, swallowing dispatch failures.

    Reactive syncs run inside signal handlers / the MQTT consumer; a broker
    outage must never abort the triggering save or message processing.
    """
    try:
        sync_indicator(binding, actor=actor)
    except DeviceCommandError:
        logger.warning("Indicator sync skipped (broker error) for binding %s", binding.pk)
    except Exception:  # pragma: no cover - defensive; never break the caller
        logger.exception("Unexpected error syncing indicator binding %s", binding.pk)


def sync_bindings_for_asset(asset_id, *, actor=None) -> int:
    """Re-sync every indicator bound to an asset. Returns the binding count."""
    if not asset_id:
        return 0
    bindings = list(_bindings_qs().filter(asset_id=asset_id))
    for binding in bindings:
        _safe_sync(binding, actor=actor)
    return len(bindings)


def sync_bindings_for_location(location_id, *, actor=None) -> int:
    """Re-sync every indicator bound to a room. Returns the binding count."""
    if not location_id:
        return 0
    bindings = list(_bindings_qs().filter(location_id=location_id))
    for binding in bindings:
        _safe_sync(binding, actor=actor)
    return len(bindings)


def flash_deny_for_asset(asset_id, *, actor=None) -> int:
    """Blink every indicator bound to an asset red as access-denied feedback.

    Transient and best-effort: each dispatch is wrapped so a broker outage (or
    an asset with no bound indicator) never propagates into the access-control
    decision path. Does not touch ``last_status`` — the firmware reverts to the
    asset's steady status after the blink. Returns the indicator count.
    """
    if not asset_id:
        return 0
    bindings = list(_bindings_qs().filter(asset_id=asset_id))
    for binding in bindings:
        try:
            dispatch_command(
                binding.device,
                dict(DENY_FLASH_PAYLOAD),
                actor=actor,
                audit_action=INDICATOR_COMMAND,
            )
        except DeviceCommandError:
            logger.warning("Indicator deny-flash skipped (broker error) for binding %s", binding.pk)
        except Exception:  # pragma: no cover - defensive; never break the caller
            logger.exception("Unexpected error flashing deny on indicator binding %s", binding.pk)
    return len(bindings)


def sync_bindings_for_device(device: ESP32Device, *, actor=None) -> int:
    """Re-sync indicators affected by a device's online/offline transition.

    Covers two cases: the indicator device itself reconnecting (refresh its
    light), and a *control* device on an asset flipping offline/online (which
    changes that asset's derived availability). Returns the binding count.
    """
    from django.db.models import Q

    asset_ids: List[Any] = list(
        AssetDevice.objects.filter(device=device).values_list("asset_id", flat=True)
    )
    query = Q(device=device)
    if asset_ids:
        query |= Q(asset_id__in=asset_ids)

    bindings = list(_bindings_qs().filter(query).distinct())
    for binding in bindings:
        _safe_sync(binding, actor=actor)
    return len(bindings)


# ---------------------------------------------------------------------------
# Explicit-preview payload validation
# ---------------------------------------------------------------------------
def _validate_int(value: Any, name: str, lo: int, hi: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        ivalue = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if ivalue < lo or ivalue > hi:
        raise ValueError(f"{name} must be between {lo} and {hi}")
    return ivalue


def _validate_color(color: Any):
    if isinstance(color, str):
        text = color.strip()
        if not text:
            raise ValueError("color must be a non-empty string")
        return text
    if isinstance(color, (list, tuple)):
        if len(color) != 3:
            raise ValueError("color array must be [r, g, b]")
        return [_validate_int(component, "color component", 0, 255) for component in color]
    raise ValueError("color must be a named string, hex string, or [r, g, b] array")


def _validate_brightness(brightness: Any):
    if isinstance(brightness, bool):
        raise ValueError("brightness must be 'low', 'high', or an integer 0-255")
    if isinstance(brightness, str):
        word = brightness.strip().lower()
        if word in VALID_BRIGHTNESS_WORDS:
            return word
        try:
            return _validate_int(int(word), "brightness", 0, 255)
        except ValueError as exc:
            raise ValueError("brightness must be 'low', 'high', or an integer 0-255") from exc
    if isinstance(brightness, int):
        return _validate_int(brightness, "brightness", 0, 255)
    raise ValueError("brightness must be 'low', 'high', or an integer 0-255")


def build_test_payload(
    *,
    color: Any = None,
    brightness: Any = None,
    pattern: Optional[str] = None,
    period_ms: Any = None,
    duration_s: Any = None,
) -> Dict[str, Any]:
    """Validate explicit indicator fields into a ``set_indicator`` payload.

    Raises ``ValueError`` on any invalid field. Requires at least one of
    color / brightness / pattern so an empty preview is rejected.
    """
    payload: Dict[str, Any] = {"cmd": INDICATOR_COMMAND}
    if color is not None:
        payload["color"] = _validate_color(color)
    if brightness is not None:
        payload["brightness"] = _validate_brightness(brightness)
    if pattern is not None:
        if pattern not in VALID_PATTERNS:
            raise ValueError(f"pattern must be one of {sorted(VALID_PATTERNS)}")
        payload["pattern"] = pattern
    if period_ms is not None:
        payload["period_ms"] = _validate_int(period_ms, "period_ms", 1, 60000)
    if duration_s is not None:
        payload["duration_s"] = _validate_int(duration_s, "duration_s", 0, 86400)
    if len(payload) == 1:
        raise ValueError("At least one of color, brightness, or pattern is required.")
    indicator = firmware_indicator(payload.get("color"), payload.get("pattern"))
    if indicator is not None:
        payload["indicator"] = indicator
    return payload


__all__ = [
    "INDICATOR_COMMAND",
    "PRESENTATIONS",
    "presentation_for_status",
    "build_payload",
    "derive_asset_status",
    "derive_room_status",
    "status_for_binding",
    "sync_indicator",
    "send_indicator_test",
    "sync_bindings_for_asset",
    "sync_bindings_for_location",
    "sync_bindings_for_device",
    "flash_deny_for_asset",
    "build_test_payload",
]
