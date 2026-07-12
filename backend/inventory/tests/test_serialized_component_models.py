"""Unit tests for SerializedComponent lifecycle + ComponentUsageEvent logging."""

from django.core.exceptions import ValidationError

import pytest

from inventory.models import (
    ComponentUsageEvent,
    InventoryItem,
    SerializedComponent,
)
from inventory.tests.factories import (
    AssetFactory,
    InventoryItemFactory,
    SerializedComponentFactory,
)

pytestmark = pytest.mark.django_db


def _consumable_item():
    return InventoryItemFactory(
        is_serialized=True,
        serial_tracking_mode=InventoryItem.SerialTrackingMode.CONSUMABLE,
    )


def _reusable_item():
    return InventoryItemFactory(
        is_serialized=True,
        serial_tracking_mode=InventoryItem.SerialTrackingMode.REUSABLE,
    )


@pytest.mark.unit
class TestSerializedComponentBasics:
    def test_defaults_to_received_and_derives_mode(self):
        item = _consumable_item()
        component = SerializedComponent.objects.create(item=item, serial_number="ABC-1")
        assert component.status == SerializedComponent.Status.RECEIVED
        assert component.tracking_mode == InventoryItem.SerialTrackingMode.CONSUMABLE
        assert str(component) == "ABC-1 (Received)"

    def test_available_actions_reflect_status_and_mode(self):
        consumable = SerializedComponentFactory()
        assert consumable.available_actions == [SerializedComponent.Action.RECEIVE]

        reusable = SerializedComponentFactory(
            item__serial_tracking_mode=InventoryItem.SerialTrackingMode.REUSABLE
        )
        reusable.status = SerializedComponent.Status.INSTALLED
        # reusable installed unit can be removed or retired
        assert set(reusable.available_actions) == {
            SerializedComponent.Action.REMOVE,
            SerializedComponent.Action.RETIRE,
        }

    def test_can_apply_reflects_transition_table(self):
        component = SerializedComponentFactory()
        assert component.can_apply(SerializedComponent.Action.RECEIVE) is True
        assert component.can_apply(SerializedComponent.Action.CONSUME) is False

    def test_unique_serial_per_item(self):
        item = _consumable_item()
        SerializedComponent.objects.create(item=item, serial_number="DUP")
        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            SerializedComponent.objects.create(item=item, serial_number="DUP")


@pytest.mark.unit
class TestConsumableLifecycle:
    def test_full_happy_path(self):
        component = SerializedComponentFactory()
        asset = AssetFactory()

        component.apply_action(SerializedComponent.Action.RECEIVE)
        assert component.status == SerializedComponent.Status.IN_STOCK
        assert component.received_at is not None

        component.apply_action(SerializedComponent.Action.INSTALL, asset=asset)
        assert component.status == SerializedComponent.Status.INSTALLED
        assert component.installed_in_asset_id == asset.id
        assert component.installed_at is not None

        consume_event = component.apply_action(SerializedComponent.Action.CONSUME)
        assert component.status == SerializedComponent.Status.CONSUMED
        assert component.installed_in_asset is None
        assert consume_event.asset_id == asset.id

        component.apply_action(SerializedComponent.Action.DISPOSE, disposal_reason="used up")
        assert component.status == SerializedComponent.Status.DISPOSED
        assert component.disposal_reason == "used up"
        assert component.disposed_at is not None

        # One event per transition, in order.
        events = list(component.usage_events.order_by("at", "created_at"))
        assert [e.action for e in events] == [
            SerializedComponent.Action.RECEIVE,
            SerializedComponent.Action.INSTALL,
            SerializedComponent.Action.CONSUME,
            SerializedComponent.Action.DISPOSE,
        ]

    def test_cannot_remove_or_retire_consumable(self):
        component = SerializedComponentFactory()
        component.apply_action(SerializedComponent.Action.RECEIVE)
        component.apply_action(SerializedComponent.Action.INSTALL, asset=AssetFactory())
        with pytest.raises(ValidationError):
            component.apply_action(SerializedComponent.Action.REMOVE)
        with pytest.raises(ValidationError):
            component.apply_action(SerializedComponent.Action.RETIRE)

    def test_cannot_skip_states(self):
        component = SerializedComponentFactory()
        # install requires in_stock first
        with pytest.raises(ValidationError):
            component.apply_action(SerializedComponent.Action.INSTALL, asset=AssetFactory())
        assert component.status == SerializedComponent.Status.RECEIVED

    def test_install_requires_asset(self):
        component = SerializedComponentFactory()
        component.apply_action(SerializedComponent.Action.RECEIVE)
        with pytest.raises(ValidationError):
            component.apply_action(SerializedComponent.Action.INSTALL)

    def test_dispose_requires_reason(self):
        component = SerializedComponentFactory()
        component.apply_action(SerializedComponent.Action.RECEIVE)
        component.apply_action(SerializedComponent.Action.INSTALL, asset=AssetFactory())
        component.apply_action(SerializedComponent.Action.CONSUME)
        with pytest.raises(ValidationError):
            component.apply_action(SerializedComponent.Action.DISPOSE)
        assert component.status == SerializedComponent.Status.CONSUMED

    def test_dispose_clears_stale_current_asset_link_but_logs_it(self):
        asset = AssetFactory()
        component = SerializedComponentFactory(
            status=SerializedComponent.Status.CONSUMED,
            installed_in_asset=asset,
        )

        event = component.apply_action(
            SerializedComponent.Action.DISPOSE,
            disposal_reason="already spent",
        )

        assert component.status == SerializedComponent.Status.DISPOSED
        assert component.installed_in_asset is None
        assert event.asset_id == asset.id


@pytest.mark.unit
class TestReusableLifecycle:
    def test_install_remove_is_repeatable(self):
        component = SerializedComponentFactory(
            item__serial_tracking_mode=InventoryItem.SerialTrackingMode.REUSABLE
        )
        asset_a = AssetFactory()
        asset_b = AssetFactory()

        component.apply_action(SerializedComponent.Action.RECEIVE)
        component.apply_action(SerializedComponent.Action.INSTALL, asset=asset_a)
        assert component.installed_in_asset_id == asset_a.id

        component.apply_action(SerializedComponent.Action.REMOVE)
        assert component.status == SerializedComponent.Status.REMOVED
        assert component.installed_in_asset is None

        # Re-install into a different asset (repeatable).
        component.apply_action(SerializedComponent.Action.INSTALL, asset=asset_b)
        assert component.status == SerializedComponent.Status.INSTALLED
        assert component.installed_in_asset_id == asset_b.id

        component.apply_action(SerializedComponent.Action.REMOVE)
        component.apply_action(SerializedComponent.Action.RETIRE)
        assert component.status == SerializedComponent.Status.RETIRED

        component.apply_action(SerializedComponent.Action.DISPOSE, disposal_reason="end of life")
        assert component.status == SerializedComponent.Status.DISPOSED

        # The remove event records the asset it was pulled from.
        remove_events = component.usage_events.filter(
            action=SerializedComponent.Action.REMOVE
        ).order_by("at")
        assert remove_events.count() == 2
        assert remove_events.first().asset_id == asset_a.id

    def test_retire_directly_from_in_stock(self):
        component = SerializedComponentFactory(
            item__serial_tracking_mode=InventoryItem.SerialTrackingMode.REUSABLE
        )
        component.apply_action(SerializedComponent.Action.RECEIVE)
        component.apply_action(SerializedComponent.Action.RETIRE)
        assert component.status == SerializedComponent.Status.RETIRED

    def test_retire_from_installed_clears_asset_but_logs_it(self):
        asset = AssetFactory()
        component = SerializedComponentFactory(
            item__serial_tracking_mode=InventoryItem.SerialTrackingMode.REUSABLE
        )
        component.apply_action(SerializedComponent.Action.RECEIVE)
        component.apply_action(SerializedComponent.Action.INSTALL, asset=asset)

        event = component.apply_action(SerializedComponent.Action.RETIRE)

        assert component.status == SerializedComponent.Status.RETIRED
        assert component.installed_in_asset is None
        assert event.asset_id == asset.id

    def test_cannot_consume_reusable(self):
        component = SerializedComponentFactory(
            item__serial_tracking_mode=InventoryItem.SerialTrackingMode.REUSABLE
        )
        component.apply_action(SerializedComponent.Action.RECEIVE)
        component.apply_action(SerializedComponent.Action.INSTALL, asset=AssetFactory())
        with pytest.raises(ValidationError):
            component.apply_action(SerializedComponent.Action.CONSUME)


@pytest.mark.unit
class TestUsageEventLogging:
    def test_event_records_actor_and_notes(self, django_user_model):
        user = django_user_model.objects.create_user(username="tech", password="x")
        component = SerializedComponentFactory()
        event = component.apply_action(
            SerializedComponent.Action.RECEIVE, actor=user, notes="unboxed"
        )
        assert isinstance(event, ComponentUsageEvent)
        assert event.actor_id == user.id
        assert event.notes == "unboxed"
        assert event.action == SerializedComponent.Action.RECEIVE

    def test_event_str_includes_serial_and_action(self):
        component = SerializedComponentFactory(serial_number="SN-STR")
        event = component.apply_action(SerializedComponent.Action.RECEIVE)
        text = str(event)
        assert "SN-STR" in text
        assert "Receive" in text

    def test_failed_transition_writes_no_event(self):
        component = SerializedComponentFactory()
        with pytest.raises(ValidationError):
            component.apply_action(SerializedComponent.Action.CONSUME)
        assert ComponentUsageEvent.objects.filter(component=component).count() == 0
