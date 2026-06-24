"""Reactive indicator sync (epic ga-72l).

When a status source changes, recompute and push the new presentation to every
indicator bound to the affected asset/room. Handlers are deliberately
fail-safe: dispatch errors are swallowed by ``services.indicator._safe_sync`` so
a broker outage never aborts the save that triggered the signal. When no
indicator is bound to the entity, the fan-out helpers no-op after a single
cheap query.

Device online/offline transitions are handled in the MQTT consumer
(``handle_status_message``) / webhook task rather than here, because those use
bulk ``.update()`` writes that don't emit ``post_save``.
"""

from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver

from inventory.models import Asset

from .models import DeviceLockout, DeviceUsage, OperationalMode, RoomOperationalMode
from .services.indicator import sync_bindings_for_asset, sync_bindings_for_location


@receiver(post_save, sender=OperationalMode, dispatch_uid="indicator_operational_mode")
def _operational_mode_changed(sender, instance, **kwargs):
    sync_bindings_for_asset(instance.asset_id)


@receiver(post_save, sender=DeviceLockout, dispatch_uid="indicator_device_lockout")
def _device_lockout_changed(sender, instance, **kwargs):
    sync_bindings_for_asset(instance.asset_id)


@receiver(post_save, sender=DeviceUsage, dispatch_uid="indicator_device_usage")
def _device_usage_changed(sender, instance, **kwargs):
    # Fires on both session start (create) and end (end_session saves ended_at).
    sync_bindings_for_asset(instance.asset_id)


@receiver(post_save, sender=RoomOperationalMode, dispatch_uid="indicator_room_mode")
def _room_mode_changed(sender, instance, **kwargs):
    sync_bindings_for_location(instance.location_id)


@receiver(post_save, sender=Asset, dispatch_uid="indicator_asset_status")
def _asset_changed(sender, instance, **kwargs):
    # An Asset save may have changed status; per-binding debounce turns an
    # unchanged presentation into a no-op, so no status diff is needed here.
    sync_bindings_for_asset(instance.pk)
