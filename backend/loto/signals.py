"""Signal handlers that keep AssetEnergySource in sync with the power topology.

Listens to ``Cable`` and ``PowerBreaker.required_loto_devices`` changes:

* connected power cable saved → re-derive the attached asset's electrical
  energy source.
* power cable decommissioned (or hard-deleted) → mark its derived row stale
  (do not delete — audit trail).
* breaker LOTO requirements changed → re-derive every asset cabled to that
  breaker so the new device set propagates.
"""

from __future__ import annotations

import logging

from django.db.models.signals import m2m_changed, post_save, pre_delete
from django.dispatch import receiver

from electrical_circuits.models import Cable, PowerBreaker
from electrical_circuits.services.power_chain import get_devices_on_breaker

from .services.power_derivation import derive_for_asset, derive_for_cable, mark_stale_for_cable

logger = logging.getLogger("loto.power_derivation")


@receiver(post_save, sender=Cable, dispatch_uid="loto_derive_on_cable_save")
def _cable_post_save(sender, instance: Cable, **kwargs) -> None:
    if instance.cable_type != Cable.CABLE_TYPE_POWER:
        return
    if instance.status == Cable.STATUS_CONNECTED:
        derive_for_cable(instance)
    else:
        # Planned / decommissioned: keep audit trail, mark historical.
        mark_stale_for_cable(instance)


@receiver(pre_delete, sender=Cable, dispatch_uid="loto_stale_on_cable_delete")
def _cable_pre_delete(sender, instance: Cable, **kwargs) -> None:
    """Mark stale BEFORE the SET_NULL cascade nulls the derived_from FK."""

    if instance.cable_type != Cable.CABLE_TYPE_POWER:
        return
    mark_stale_for_cable(instance)


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
    for asset in get_devices_on_breaker(instance):
        derive_for_asset(asset)
