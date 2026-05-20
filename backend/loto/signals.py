"""Signal handlers that keep AssetEnergySource in sync with the power topology.

Listens for changes that affect the asset → breaker derivation:

* asset's breaker changes → re-derive its electrical energy source.
* breaker LOTO requirements changed → re-derive every asset on that breaker
  so the new device set propagates.
* breaker is being deleted → mark its derived rows stale so the audit trail
  survives the cascade.
"""

from __future__ import annotations

import logging

from django.db.models.signals import m2m_changed, post_save, pre_delete
from django.dispatch import receiver

from electrical_circuits.models import PowerBreaker
from electrical_circuits.services.power_chain import get_devices_on_breaker
from inventory.models import Asset

from .services.power_derivation import derive_for_asset, derive_for_breaker, mark_stale_for_breaker

logger = logging.getLogger("loto.power_derivation")


@receiver(post_save, sender=Asset, dispatch_uid="loto_derive_on_asset_save")
def _asset_post_save(sender, instance: Asset, **kwargs) -> None:
    """Re-derive whenever an asset is saved.

    Cheap: it's one breaker lookup + one update_or_create when ``breaker_id``
    is set, or a single ``UPDATE ... SET is_stale=true`` when it is null.
    """

    derive_for_asset(instance)


@receiver(pre_delete, sender=PowerBreaker, dispatch_uid="loto_stale_on_breaker_delete")
def _breaker_pre_delete(sender, instance: PowerBreaker, **kwargs) -> None:
    """Mark stale BEFORE the SET_NULL cascade nulls the derived_from FK."""

    mark_stale_for_breaker(instance)


@receiver(
    m2m_changed,
    sender=PowerBreaker.required_loto_devices.through,
    dispatch_uid="loto_propagate_breaker_devices",
)
def _breaker_devices_changed(sender, instance, action: str, **kwargs) -> None:
    """Push breaker LOTO-device edits down to every derived row on the breaker."""

    if action not in {"post_add", "post_remove", "post_clear"}:
        return
    if not isinstance(instance, PowerBreaker):
        # Reverse signal (LOTODevice.breakers.add(...)) — not modeled here.
        return
    derive_for_breaker(instance)
    # Defensive: ensure any asset still on the breaker (but missing a derived
    # row) gets one.
    for asset in get_devices_on_breaker(instance):
        derive_for_asset(asset)
