"""
Signal wiring for AssetEnergySource auto-derivation (oms-qc3).

* PowerCable post_save → derive (or mark historical if decommissioned).
* PowerCable pre_delete → mark derived rows historical (so the audit trail
  survives the SET_NULL that follows when the cable is deleted).
* PowerBreaker.lockout_devices m2m_changed → re-sync required_devices on
  every derived row downstream of the breaker.
"""

from __future__ import annotations

from django.db.models.signals import m2m_changed, post_save, pre_delete
from django.dispatch import receiver

from electrical_circuits.models import Cable, PowerBreaker, PowerOutlet

from .models import AssetEnergySource, LOTODevice
from .services.derivation import (
    derive_energy_source_for_cable,
    mark_cable_derived_sources_historical,
)


@receiver(post_save, sender=Cable)
def cable_saved(sender, instance: Cable, **kwargs):
    if instance.cable_type != Cable.CABLE_TYPE_POWER:
        return
    derive_energy_source_for_cable(instance)


@receiver(pre_delete, sender=Cable)
def cable_deleted(sender, instance: Cable, **kwargs):
    if instance.cable_type != Cable.CABLE_TYPE_POWER:
        return
    mark_cable_derived_sources_historical(instance.pk)


def _resync_breaker(breaker: PowerBreaker) -> None:
    devices = list(breaker.lockout_devices.all())
    derived = AssetEnergySource.objects.filter(
        derived_from__cable_type=Cable.CABLE_TYPE_POWER,
        derived_from__isnull=False,
    )
    # Narrow to derived rows whose cable terminates on an outlet fed by this
    # breaker. Done in Python because the cable's outlet endpoint is on a
    # generic FK and can't be joined against in SQL.
    outlet_ids = set(
        PowerOutlet.objects.filter(circuit__breaker=breaker).values_list("pk", flat=True)
    )
    if not outlet_ids:
        return
    str_outlet_ids = {str(pk) for pk in outlet_ids}
    for source in derived.select_related("derived_from"):
        cable = source.derived_from
        if cable is None:
            continue
        a_id = str(cable.endpoint_a_object_id)
        b_id = str(cable.endpoint_b_object_id)
        if a_id in str_outlet_ids or b_id in str_outlet_ids:
            source.required_devices.set(devices)


@receiver(m2m_changed, sender=LOTODevice.secured_breakers.through)
def secured_breakers_changed(sender, instance, action, reverse, pk_set, **kwargs):
    if action not in ("post_add", "post_remove", "post_clear"):
        return
    if reverse:
        # ``instance`` is a PowerBreaker; one breaker affected.
        _resync_breaker(instance)
        return
    # Forward: ``instance`` is a LOTODevice, pk_set is breaker ids.
    if not pk_set:
        # post_clear leaves pk_set empty; resync everything the device
        # *used to* affect by walking all breakers it could touch. Cheaper to
        # just resync any breaker whose lockout_devices currently
        # references this device (post_clear already cleared, so we instead
        # resync every breaker — uncommon admin action, kept simple).
        for breaker in PowerBreaker.objects.all():
            _resync_breaker(breaker)
        return
    for breaker in PowerBreaker.objects.filter(pk__in=pk_set):
        _resync_breaker(breaker)
