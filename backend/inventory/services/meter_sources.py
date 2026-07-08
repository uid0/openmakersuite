"""Asset meter source framework + runtime rollup (EAM bead-1).

This module is the engine behind :class:`inventory.models.AssetMeter`. A meter
is a named cumulative counter on an asset; HOW it advances is decided by a
*source adapter* keyed on ``meter.source``:

* ``auto_session`` (:class:`SessionRuntimeAdapter`) — PULL. On a 15-minute beat
  it folds ended ``forgekey.DeviceUsage`` sessions into runtime-hour readings,
  using ``meter.rollup_watermark_at`` as an exactly-once watermark.
* ``auto_telemetry`` (:class:`TelemetryCounterAdapter`) — PUSH stub. Registered
  and ready for the MQTT flow/counter ingestion, which is a NOTED FOLLOW-UP
  (Ian: manual first). ``pull()`` returns ``[]``.
* ``manual`` (:class:`ManualAdapter`) — PUSH. Readings arrive from the
  record-reading / adjust API actions. ``pull()`` returns ``[]``.

The design mirrors the "logic lives in the service, the Celery task is a thin
shell" pattern (see ``forgekey/tasks.py`` →
``forgekey.services.access_control.end_idle_sessions``): everything here is
plain functions/classes so it is unit-testable without Celery, and
:func:`inventory.tasks.roll_up_meters` just calls :func:`run_rollup`.

New sources plug in by subclassing :class:`MeterSourceAdapter` and calling
:func:`register_meter_source` — the rollup job never has to change.
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from ..models import Asset, AssetMeter, AssetMeterReading

logger = logging.getLogger(__name__)


@dataclass
class ReadingSpec:
    """A pending reading an adapter (or a push handler) wants applied to a meter.

    Exactly one of ``delta`` / ``absolute`` must be set — :func:`apply_reading`
    collapses both into the same ledger row:

    * ``absolute`` → ``value_after = absolute``, ``delta = absolute - current``
    * ``delta``    → ``value_after = current + delta``

    ``watermark_at`` (auto_session only) advances ``meter.rollup_watermark_at``
    atomically with the reading, so a re-run consumes each session exactly once.
    """

    source: str
    observed_at: datetime
    delta: Optional[Decimal] = None
    absolute: Optional[Decimal] = None
    is_estimated: bool = False
    source_ref: str = ""
    notes: str = ""
    watermark_at: Optional[datetime] = None
    extra: Dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (self.delta is None) == (self.absolute is None):
            raise ValueError("ReadingSpec requires exactly one of delta / absolute")


class MeterSourceAdapter(abc.ABC):
    """Base class for a meter source.

    ``source_slug`` matches :class:`inventory.models.AssetMeter` ``source``
    choices. ``pull(meter, now)`` returns the readings the rollup should apply
    for this meter right now — ``[]`` for PUSH sources whose readings arrive
    out-of-band (manual entry, telemetry).
    """

    source_slug: str

    @abc.abstractmethod
    def pull(self, meter: AssetMeter, now: datetime) -> List[ReadingSpec]:
        """Return readings to apply for ``meter`` at ``now`` (``[]`` if none)."""
        raise NotImplementedError


_REGISTRY: Dict[str, MeterSourceAdapter] = {}


def register_meter_source(slug: str, adapter: MeterSourceAdapter) -> None:
    """Register ``adapter`` as the handler for meters whose ``source == slug``."""
    _REGISTRY[slug] = adapter


def get_meter_source(slug: str) -> Optional[MeterSourceAdapter]:
    """Return the registered adapter for ``slug`` (``None`` if unregistered)."""
    return _REGISTRY.get(slug)


class SessionRuntimeAdapter(MeterSourceAdapter):
    """auto_session (PULL): fold ended usage sessions into runtime-hour readings.

    Sums ``duration_seconds`` over every ``forgekey.DeviceUsage`` for the meter's
    asset that ended AFTER ``meter.rollup_watermark_at`` (all of history on the
    first run), converts to hours, and emits ONE reading whose ``observed_at`` /
    new watermark is the latest ``ended_at`` in the batch. Only *ended* sessions
    are eligible (``duration_seconds`` is set alongside ``ended_at`` in
    ``DeviceUsage.end_session``), so an in-flight session is never counted and a
    re-run is exactly-once.
    """

    source_slug = AssetMeter.SOURCE_AUTO_SESSION

    def pull(self, meter: AssetMeter, now: datetime) -> List[ReadingSpec]:
        # Lazy import: inventory must not migrate/import against forgekey at
        # module load (avoids a circular app dependency). Established pattern in
        # inventory/views.py.
        from forgekey.models import DeviceUsage

        qs = DeviceUsage.objects.filter(
            asset_id=meter.asset_id,
            duration_seconds__isnull=False,
        )
        watermark = meter.rollup_watermark_at
        if watermark is not None:
            qs = qs.filter(ended_at__gt=watermark)
        sessions = list(qs.order_by("ended_at").values_list("ended_at", "duration_seconds"))
        if not sessions:
            return []

        total_seconds = sum(duration for _, duration in sessions)
        max_ended = max(ended_at for ended_at, _ in sessions)
        if total_seconds <= 0:
            # Every new session was zero-length; nothing to add, but advance the
            # watermark so we don't re-scan them forever.
            return [
                ReadingSpec(
                    source=AssetMeterReading.SOURCE_AUTO_SESSION,
                    observed_at=max_ended,
                    delta=Decimal("0"),
                    is_estimated=False,
                    source_ref=f"device_usage x{len(sessions)} ≤{max_ended.isoformat()}",
                    watermark_at=max_ended,
                )
            ]

        delta_hours = (Decimal(total_seconds) / Decimal(3600)).quantize(Decimal("0.0001"))
        return [
            ReadingSpec(
                source=AssetMeterReading.SOURCE_AUTO_SESSION,
                observed_at=max_ended,
                delta=delta_hours,
                is_estimated=False,
                source_ref=f"device_usage x{len(sessions)} ≤{max_ended.isoformat()}",
                watermark_at=max_ended,
            )
        ]


class TelemetryCounterAdapter(MeterSourceAdapter):
    """auto_telemetry (PUSH): registered stub for MQTT flow/counter ingestion.

    NOTED FOLLOW-UP (not this bead — Ian: manual first): an MQTT handler will
    receive gallons/cycle counts from a flow sensor and push them straight into
    the ledger. That handler builds a :class:`ReadingSpec` and calls
    :func:`apply_reading` directly, e.g.::

        spec = ReadingSpec(
            source=AssetMeterReading.SOURCE_AUTO_TELEMETRY,
            observed_at=sensor_ts,
            delta=gallons_since_last,          # or absolute=cumulative_counter
            is_estimated=False,
            source_ref=f"mqtt:{topic}",
        )
        apply_reading(meter, spec)

    Nothing is pulled on the schedule, so ``pull()`` returns ``[]``.
    """

    source_slug = AssetMeter.SOURCE_AUTO_TELEMETRY

    def pull(self, meter: AssetMeter, now: datetime) -> List[ReadingSpec]:
        return []


class ManualAdapter(MeterSourceAdapter):
    """manual (PUSH): readings come from the record-reading / adjust API actions."""

    source_slug = AssetMeter.SOURCE_MANUAL

    def pull(self, meter: AssetMeter, now: datetime) -> List[ReadingSpec]:
        return []


register_meter_source(AssetMeter.SOURCE_AUTO_SESSION, SessionRuntimeAdapter())
register_meter_source(AssetMeter.SOURCE_AUTO_TELEMETRY, TelemetryCounterAdapter())
register_meter_source(AssetMeter.SOURCE_MANUAL, ManualAdapter())


def apply_reading(
    meter: AssetMeter,
    spec: ReadingSpec,
    *,
    recorded_by=None,
) -> AssetMeterReading:
    """Apply ``spec`` to ``meter``: write the ledger row and bump the cache.

    Everything happens inside one transaction under a row lock on the meter, so
    concurrent readings serialize and ``value_after`` is always computed from the
    freshest ``current_value``. Absolute and delta reads collapse to the same
    row (see :class:`ReadingSpec`).

    hours_used DUAL-WRITE (zero-regression shim): for a ``runtime_hours`` meter a
    positive advance is also added to ``Asset.hours_used`` via an ``F()`` update
    (the exact pattern behind the log-hours endpoint), so the EXISTING forecast —
    which reads ``Asset.hours_used`` — keeps working untouched until bead-2
    migrates it to read meters. Downward corrections do not decrement hours_used
    (it is a monotonic cumulative counter).
    """
    with transaction.atomic():
        locked = AssetMeter.objects.select_for_update().get(pk=meter.pk)

        if spec.absolute is not None:
            value_after = spec.absolute
            delta = value_after - locked.current_value
        else:
            delta = spec.delta
            value_after = locked.current_value + delta

        reading = AssetMeterReading.objects.create(
            meter=locked,
            source=spec.source,
            delta=delta,
            value_after=value_after,
            is_estimated=spec.is_estimated,
            observed_at=spec.observed_at,
            recorded_by=recorded_by,
            source_ref=spec.source_ref,
            notes=spec.notes,
        )

        locked.current_value = value_after
        locked.current_is_estimated = spec.is_estimated
        update_fields = ["current_value", "current_is_estimated", "updated_at"]
        if spec.watermark_at is not None:
            locked.rollup_watermark_at = spec.watermark_at
            update_fields.append("rollup_watermark_at")
        locked.save(update_fields=update_fields)

        if locked.meter_type == AssetMeter.RUNTIME_HOURS:
            increment = int(delta)
            if increment > 0:
                Asset.objects.filter(pk=locked.asset_id).update(
                    hours_used=F("hours_used") + increment
                )

    # Reflect the applied values back onto the caller's instance for convenience.
    meter.current_value = value_after
    meter.current_is_estimated = spec.is_estimated
    if spec.watermark_at is not None:
        meter.rollup_watermark_at = spec.watermark_at
    return reading


def run_rollup(now: Optional[datetime] = None) -> Dict[str, int]:
    """Advance every active meter by dispatching to its source adapter.

    Iterates active meters, asks each meter's registered adapter to ``pull()``
    the readings due now, and applies them. A failure on one meter is logged and
    does not block the others (mirrors ``advance_firmware_rollouts``). Returns a
    small stats dict for the task's return value / logs.
    """
    if now is None:
        now = timezone.now()

    stats = {"meters_scanned": 0, "readings_applied": 0, "errors": 0}
    for meter in AssetMeter.objects.filter(is_active=True).iterator():
        adapter = get_meter_source(meter.source)
        if adapter is None:
            logger.warning("No meter source adapter registered for %r", meter.source)
            continue
        stats["meters_scanned"] += 1
        try:
            for spec in adapter.pull(meter, now):
                apply_reading(meter, spec)
                stats["readings_applied"] += 1
        except Exception:  # noqa: BLE001 - one bad meter must not stop the sweep
            stats["errors"] += 1
            logger.exception("Meter rollup failed for meter %s (%s)", meter.pk, meter.name)

    return stats
