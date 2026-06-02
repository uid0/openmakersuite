"""
Sync helpers for BMS bindings — pull current state from the adapter
and upsert it onto each ``ThermostatBinding`` row.

Kept in a plain module (not on the model) so the management command,
future Celery beat task, and any other consumer all hit the same path.
Per-binding errors are captured into the row's ``last_sync_error`` and
do not abort the wider sweep — one offline thermostat shouldn't take
down sync for the rest of the fleet.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, List

from django.utils import timezone

from .adapters import BmsAdapter, BmsAdapterError, adapter_for
from .models import BmsConfig

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    config_id: str
    config_name: str
    succeeded: List[str]  # external_device_id values
    failed: List[tuple]  # (external_device_id, error_message)


def sync_config(config: BmsConfig, *, adapter: BmsAdapter | None = None) -> SyncResult:
    """Pull state for every binding under ``config``. Updates each row
    in place. Records the config-level success / failure summary on the
    parent ``BmsConfig`` so the admin can see why a sweep is sad.

    Pass ``adapter`` explicitly to inject a MockAdapter in tests; in
    prod call sites just pass the config and let ``adapter_for`` resolve.
    """
    a = adapter or adapter_for(config)
    succeeded: List[str] = []
    failed: List[tuple] = []
    bindings = list(config.bindings.all())
    for binding in bindings:
        try:
            state = a.get_state(binding.external_device_id, binding.external_location_id)
        except BmsAdapterError as exc:
            logger.warning(
                "bms sync: binding %s on config %s failed: %s",
                binding.id,
                config.id,
                exc,
            )
            binding.last_sync_error = str(exc)[:5000]
            binding.last_synced_at = timezone.now()
            binding.save(update_fields=["last_sync_error", "last_synced_at", "updated_at"])
            failed.append((binding.external_device_id, str(exc)))
            continue
        except Exception as exc:
            # Unexpected — don't swallow silently; tag the binding and
            # re-raise so Sentry catches the bug.
            logger.exception("bms sync: binding %s unexpected exception", binding.id)
            binding.last_sync_error = f"unexpected: {type(exc).__name__}: {exc}"[:5000]
            binding.last_synced_at = timezone.now()
            binding.save(update_fields=["last_sync_error", "last_synced_at", "updated_at"])
            raise

        binding.indoor_temp_f = state.indoor_temp_f
        binding.indoor_humidity_pct = state.indoor_humidity_pct
        binding.cool_setpoint_f = state.cool_setpoint_f
        binding.heat_setpoint_f = state.heat_setpoint_f
        binding.hvac_mode = state.hvac_mode
        binding.fan_mode = state.fan_mode
        binding.state_raw = state.raw or {}
        binding.last_synced_at = timezone.now()
        binding.last_sync_error = ""
        binding.save(
            update_fields=[
                "indoor_temp_f",
                "indoor_humidity_pct",
                "cool_setpoint_f",
                "heat_setpoint_f",
                "hvac_mode",
                "fan_mode",
                "state_raw",
                "last_synced_at",
                "last_sync_error",
                "updated_at",
            ]
        )
        succeeded.append(binding.external_device_id)

    config.last_synced_at = timezone.now()
    if failed:
        # Summarize without dumping the full per-binding error list (could
        # be huge); detail lives on the binding rows themselves.
        config.last_sync_error = (
            f"{len(failed)}/{len(bindings)} bindings failed: "
            f"{', '.join(d for d, _ in failed[:5])}" + (" ..." if len(failed) > 5 else "")
        )
    else:
        config.last_sync_error = ""
    config.save(update_fields=["last_synced_at", "last_sync_error", "updated_at"])

    return SyncResult(
        config_id=str(config.id),
        config_name=config.name,
        succeeded=succeeded,
        failed=failed,
    )


def sync_all(configs: Iterable[BmsConfig] | None = None) -> List[SyncResult]:
    """Run ``sync_config`` against every active ``BmsConfig`` (or the
    explicit iterable). Returns one ``SyncResult`` per config."""
    if configs is None:
        configs = BmsConfig.objects.filter(is_active=True)
    return [sync_config(c) for c in configs]
