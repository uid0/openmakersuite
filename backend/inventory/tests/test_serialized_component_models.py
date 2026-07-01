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
        serial_tracking_mode=InventoryItem.SERIAL_TRACKING_CONSUMABLE,
    )


def _reusable_item():
    return InventoryItemFactory(
        is_serialized=True,
        serial_tracking_mode=InventoryItem.SERIAL_TRACKING_REUSABLE,
    )


@pytest.mark.unit
class TestSerializedComponentBasics:
    def test_defaults_to_received_and_derives_mode(self):
        item = _consumable_item()
        component = SerializedComponent.objects.create(item=item, serial_number="ABC-1")
        assert component.status == SerializedComponent.RECEIVED
        assert component.tracking_mode == InventoryItem.SERIAL_TRACKING_CONSUMABLE
        assert str(component) == "ABC-1 (Received)"

    def test_available_actions_reflect_status_and_mode(self):
        consumable = SerializedComponentFactory()
        assert consumable.available_actions == [SerializedComponent.ACTION_RECEIVE]

        reusable = SerializedComponentFactory(
            item__serial_tracking_mode=InventoryItem.SERIAL_TRACKING_REUSABLE
        )
        reusable.status = SerializedComponent.INSTALLED
        # reusable installed unit can be removed or retired
        assert set(reusable.available_actions) == {
            SerializedComponent.ACTION_REMOVE,
            SerializedComponent.ACTION_RETIRE,
        }

    def test_can_apply_reflects_transition_table(self):
        component = SerializedComponentFactory()
        assert component.can_apply(SerializedComponent.ACTION_RECEIVE) is True
        assert component.can_apply(SerializedComponent.ACTION_CONSUME) is False

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

        component.apply_action(SerializedComponent.ACTION_RECEIVE)
        assert component.status == SerializedComponent.IN_STOCK
        assert component.received_at is not None

        component.apply_action(SerializedComponent.ACTION_INSTALL, asset=asset)
        assert component.status == SerializedComponent.INSTALLED
        assert component.installed_in_asset_id == asset.id
        assert component.installed_at is not None

        component.apply_action(SerializedComponent.ACTION_CONSUME)
        assert component.status == SerializedComponent.CONSUMED

        component.apply_action(SerializedComponent.ACTION_DISPOSE, disposal_reason="used up")
        assert component.status == SerializedComponent.DISPOSED
        assert component.disposal_reason == "used up"
        assert component.disposed_at is not None

        # One event per transition, in order.
        events = list(component.usage_events.order_by("at", "created_at"))
        assert [e.action for e in events] == [
            SerializedComponent.ACTION_RECEIVE,
            SerializedComponent.ACTION_INSTALL,
            SerializedComponent.ACTION_CONSUME,
            SerializedComponent.ACTION_DISPOSE,
        ]

    def test_cannot_remove_or_retire_consumable(self):
        component = SerializedComponentFactory()
        component.apply_action(SerializedComponent.ACTION_RECEIVE)
        component.apply_action(SerializedComponent.ACTION_INSTALL, asset=AssetFactory())
        with pytest.raises(ValidationError):
            component.apply_action(SerializedComponent.ACTION_REMOVE)
        with pytest.raises(ValidationError):
            component.apply_action(SerializedComponent.ACTION_RETIRE)

    def test_cannot_skip_states(self):
        component = SerializedComponentFactory()
        # install requires in_stock first
        with pytest.raises(ValidationError):
            component.apply_action(SerializedComponent.ACTION_INSTALL, asset=AssetFactory())
        assert component.status == SerializedComponent.RECEIVED

    def test_install_requires_asset(self):
        component = SerializedComponentFactory()
        component.apply_action(SerializedComponent.ACTION_RECEIVE)
        with pytest.raises(ValidationError):
            component.apply_action(SerializedComponent.ACTION_INSTALL)

    def test_dispose_requires_reason(self):
        component = SerializedComponentFactory()
        component.apply_action(SerializedComponent.ACTION_RECEIVE)
        component.apply_action(SerializedComponent.ACTION_INSTALL, asset=AssetFactory())
        component.apply_action(SerializedComponent.ACTION_CONSUME)
        with pytest.raises(ValidationError):
            component.apply_action(SerializedComponent.ACTION_DISPOSE)
        assert component.status == SerializedComponent.CONSUMED


@pytest.mark.unit
class TestReusableLifecycle:
    def test_install_remove_is_repeatable(self):
        component = SerializedComponentFactory(
            item__serial_tracking_mode=InventoryItem.SERIAL_TRACKING_REUSABLE
        )
        asset_a = AssetFactory()
        asset_b = AssetFactory()

        component.apply_action(SerializedComponent.ACTION_RECEIVE)
        component.apply_action(SerializedComponent.ACTION_INSTALL, asset=asset_a)
        assert component.installed_in_asset_id == asset_a.id

        component.apply_action(SerializedComponent.ACTION_REMOVE)
        assert component.status == SerializedComponent.REMOVED
        assert component.installed_in_asset is None

        # Re-install into a different asset (repeatable).
        component.apply_action(SerializedComponent.ACTION_INSTALL, asset=asset_b)
        assert component.status == SerializedComponent.INSTALLED
        assert component.installed_in_asset_id == asset_b.id

        component.apply_action(SerializedComponent.ACTION_REMOVE)
        component.apply_action(SerializedComponent.ACTION_RETIRE)
        assert component.status == SerializedComponent.RETIRED

        component.apply_action(SerializedComponent.ACTION_DISPOSE, disposal_reason="end of life")
        assert component.status == SerializedComponent.DISPOSED

        # The remove event records the asset it was pulled from.
        remove_events = component.usage_events.filter(
            action=SerializedComponent.ACTION_REMOVE
        ).order_by("at")
        assert remove_events.count() == 2
        assert remove_events.first().asset_id == asset_a.id

    def test_retire_directly_from_in_stock(self):
        component = SerializedComponentFactory(
            item__serial_tracking_mode=InventoryItem.SERIAL_TRACKING_REUSABLE
        )
        component.apply_action(SerializedComponent.ACTION_RECEIVE)
        component.apply_action(SerializedComponent.ACTION_RETIRE)
        assert component.status == SerializedComponent.RETIRED

    def test_cannot_consume_reusable(self):
        component = SerializedComponentFactory(
            item__serial_tracking_mode=InventoryItem.SERIAL_TRACKING_REUSABLE
        )
        component.apply_action(SerializedComponent.ACTION_RECEIVE)
        component.apply_action(SerializedComponent.ACTION_INSTALL, asset=AssetFactory())
        with pytest.raises(ValidationError):
            component.apply_action(SerializedComponent.ACTION_CONSUME)


@pytest.mark.unit
class TestUsageEventLogging:
    def test_event_records_actor_and_notes(self, django_user_model):
        user = django_user_model.objects.create_user(username="tech", password="x")
        component = SerializedComponentFactory()
        event = component.apply_action(
            SerializedComponent.ACTION_RECEIVE, actor=user, notes="unboxed"
        )
        assert isinstance(event, ComponentUsageEvent)
        assert event.actor_id == user.id
        assert event.notes == "unboxed"
        assert event.action == SerializedComponent.ACTION_RECEIVE

    def test_event_str_includes_serial_and_action(self):
        component = SerializedComponentFactory(serial_number="SN-STR")
        event = component.apply_action(SerializedComponent.ACTION_RECEIVE)
        text = str(event)
        assert "SN-STR" in text
        assert "Receive" in text

    def test_failed_transition_writes_no_event(self):
        component = SerializedComponentFactory()
        with pytest.raises(ValidationError):
            component.apply_action(SerializedComponent.ACTION_CONSUME)
        assert ComponentUsageEvent.objects.filter(component=component).count() == 0
