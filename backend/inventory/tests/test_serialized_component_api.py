"""API tests for SerializedComponentViewSet + ComponentUsageEventViewSet."""

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils.crypto import get_random_string

import pytest
from rest_framework.test import APIClient

from inventory.models import ComponentUsageEvent, InventoryItem, SerializedComponent
from inventory.tests.factories import (
    AssetFactory,
    InventoryItemFactory,
    SerializedComponentFactory,
)

User = get_user_model()
pytestmark = pytest.mark.django_db

LIST_URL = reverse("serialized-component-list")


def _user(username, **flags):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=get_random_string(24),
        **flags,
    )


def _client(user=None):
    c = APIClient()
    if user is not None:
        c.force_authenticate(user=user)
    return c


def _action_url(component, name):
    return reverse(f"serialized-component-{name}", kwargs={"pk": str(component.id)})


@pytest.fixture
def consumable_item():
    return InventoryItemFactory(
        is_serialized=True,
        serial_tracking_mode=InventoryItem.SERIAL_TRACKING_CONSUMABLE,
    )


@pytest.fixture
def reusable_item():
    return InventoryItemFactory(
        is_serialized=True,
        serial_tracking_mode=InventoryItem.SERIAL_TRACKING_REUSABLE,
    )


@pytest.mark.integration
class TestSerializedComponentAccess:
    def test_anonymous_cannot_list(self):
        resp = _client().get(LIST_URL)
        assert resp.status_code in (401, 403)

    def test_member_can_list(self):
        SerializedComponentFactory()
        resp = _client(_user("member")).get(LIST_URL)
        assert resp.status_code == 200
        assert resp.data["count"] == 1

    def test_volunteer_cannot_create(self, consumable_item):
        resp = _client(_user("volunteer")).post(
            LIST_URL,
            data={"item": str(consumable_item.id), "serial_number": "SN-X"},
            format="json",
        )
        assert resp.status_code == 403


@pytest.mark.integration
class TestSerializedComponentCreate:
    def test_staff_can_create_starts_received(self, consumable_item):
        resp = _client(_user("staff", is_staff=True)).post(
            LIST_URL,
            data={"item": str(consumable_item.id), "serial_number": "SN-100", "lot": "L1"},
            format="json",
        )
        assert resp.status_code == 201
        assert resp.data["status"] == SerializedComponent.RECEIVED
        assert resp.data["tracking_mode"] == InventoryItem.SERIAL_TRACKING_CONSUMABLE
        assert resp.data["available_actions"] == [SerializedComponent.ACTION_RECEIVE]

    def test_cannot_create_for_non_serialized_item(self):
        plain_item = InventoryItemFactory(is_serialized=False)
        resp = _client(_user("staff2", is_staff=True)).post(
            LIST_URL,
            data={"item": str(plain_item.id), "serial_number": "SN-200"},
            format="json",
        )
        assert resp.status_code == 400
        # Serializer ValidationErrors are wrapped in the project error envelope.
        assert "item" in resp.data["error"]["details"]

    def test_create_records_provenance(self, consumable_item):
        resp = _client(_user("staff3", is_staff=True)).post(
            LIST_URL,
            data={"item": str(consumable_item.id), "serial_number": "SN-P"},
            format="json",
        )
        assert resp.status_code == 201


@pytest.mark.integration
class TestSerializedComponentLifecycleApi:
    def test_full_consumable_flow(self, consumable_item):
        staff = _user("staff4", is_staff=True)
        client = _client(staff)
        component = SerializedComponentFactory(item=consumable_item)
        asset = AssetFactory()

        r1 = client.post(_action_url(component, "receive"), data={}, format="json")
        assert r1.status_code == 200
        assert r1.data["status"] == SerializedComponent.IN_STOCK
        assert r1.data["event"]["action"] == SerializedComponent.ACTION_RECEIVE

        r2 = client.post(
            _action_url(component, "install"),
            data={"asset": str(asset.id), "notes": "into printer"},
            format="json",
        )
        assert r2.status_code == 200
        assert r2.data["status"] == SerializedComponent.INSTALLED
        assert str(r2.data["installed_in_asset"]) == str(asset.id)

        r3 = client.post(_action_url(component, "consume"), data={}, format="json")
        assert r3.status_code == 200
        assert r3.data["status"] == SerializedComponent.CONSUMED

        r4 = client.post(
            _action_url(component, "dispose"),
            data={"disposal_reason": "spent"},
            format="json",
        )
        assert r4.status_code == 200
        assert r4.data["status"] == SerializedComponent.DISPOSED

        component.refresh_from_db()
        assert component.usage_events.count() == 4
        # Actor is recorded from the request user.
        assert component.usage_events.filter(actor=staff).count() == 4

    def test_install_requires_asset(self, consumable_item):
        client = _client(_user("staff5", is_staff=True))
        component = SerializedComponentFactory(item=consumable_item)
        client.post(_action_url(component, "receive"), data={}, format="json")
        resp = client.post(_action_url(component, "install"), data={}, format="json")
        assert resp.status_code == 400

    def test_install_with_unknown_asset_returns_400(self, consumable_item):
        client = _client(_user("staff5b", is_staff=True))
        component = SerializedComponentFactory(item=consumable_item)
        client.post(_action_url(component, "receive"), data={}, format="json")
        resp = client.post(
            _action_url(component, "install"),
            data={"asset": "00000000-0000-0000-0000-000000000000"},
            format="json",
        )
        assert resp.status_code == 400
        assert "asset" in resp.data

    def test_install_with_malformed_asset_returns_400(self, consumable_item):
        client = _client(_user("staff5c", is_staff=True))
        component = SerializedComponentFactory(item=consumable_item)
        client.post(_action_url(component, "receive"), data={}, format="json")
        resp = client.post(
            _action_url(component, "install"),
            data={"asset": "not-a-valid-uuid"},
            format="json",
        )
        assert resp.status_code == 400
        assert "asset" in resp.data

    def test_invalid_transition_returns_400(self, consumable_item):
        client = _client(_user("staff6", is_staff=True))
        component = SerializedComponentFactory(item=consumable_item)
        # consume straight from received is illegal
        resp = client.post(_action_url(component, "consume"), data={}, format="json")
        assert resp.status_code == 400
        component.refresh_from_db()
        assert component.status == SerializedComponent.RECEIVED

    def test_dispose_requires_reason(self, consumable_item):
        client = _client(_user("staff7", is_staff=True))
        component = SerializedComponentFactory(item=consumable_item)
        client.post(_action_url(component, "receive"), data={}, format="json")
        client.post(
            _action_url(component, "install"),
            data={"asset": str(AssetFactory().id)},
            format="json",
        )
        client.post(_action_url(component, "consume"), data={}, format="json")
        resp = client.post(_action_url(component, "dispose"), data={}, format="json")
        assert resp.status_code == 400

    def test_volunteer_cannot_run_action(self, consumable_item):
        component = SerializedComponentFactory(item=consumable_item)
        resp = _client(_user("vol2")).post(
            _action_url(component, "receive"), data={}, format="json"
        )
        assert resp.status_code == 403

    def test_reusable_install_remove_repeatable(self, reusable_item):
        client = _client(_user("staff8", is_staff=True))
        component = SerializedComponentFactory(item=reusable_item)
        asset = AssetFactory()

        client.post(_action_url(component, "receive"), data={}, format="json")
        client.post(
            _action_url(component, "install"),
            data={"asset": str(asset.id)},
            format="json",
        )
        r_remove = client.post(_action_url(component, "remove"), data={}, format="json")
        assert r_remove.status_code == 200
        assert r_remove.data["status"] == SerializedComponent.REMOVED
        assert r_remove.data["installed_in_asset"] is None

        r_reinstall = client.post(
            _action_url(component, "install"),
            data={"asset": str(asset.id)},
            format="json",
        )
        assert r_reinstall.status_code == 200
        assert r_reinstall.data["status"] == SerializedComponent.INSTALLED


@pytest.mark.integration
class TestSerializedComponentFilters:
    def test_filter_by_status_and_item(self, consumable_item):
        other_item = InventoryItemFactory(is_serialized=True)
        c1 = SerializedComponentFactory(item=consumable_item)
        SerializedComponentFactory(item=other_item)
        c1.apply_action(SerializedComponent.ACTION_RECEIVE)  # -> in_stock

        client = _client(_user("member2"))

        by_item = client.get(LIST_URL, {"item": str(consumable_item.id)})
        assert by_item.data["count"] == 1

        by_status = client.get(LIST_URL, {"status": SerializedComponent.IN_STOCK})
        assert by_status.data["count"] == 1
        assert by_status.data["results"][0]["id"] == str(c1.id)

    def test_filter_by_installed_in_asset(self, consumable_item):
        asset = AssetFactory()
        component = SerializedComponentFactory(item=consumable_item)
        component.apply_action(SerializedComponent.ACTION_RECEIVE)
        component.apply_action(SerializedComponent.ACTION_INSTALL, asset=asset)
        SerializedComponentFactory(item=consumable_item)  # not installed

        resp = _client(_user("member4")).get(LIST_URL, {"installed_in_asset": str(asset.id)})
        assert resp.data["count"] == 1
        assert resp.data["results"][0]["id"] == str(component.id)


@pytest.mark.integration
class TestInventoryItemSerializesSerialConfig:
    """The item endpoint exposes the serialization config the UI branches on."""

    def test_item_detail_exposes_serial_fields(self):
        item = InventoryItemFactory(
            is_serialized=True,
            serial_tracking_mode=InventoryItem.SERIAL_TRACKING_REUSABLE,
        )
        url = reverse("inventoryitem-detail", kwargs={"pk": item.pk})
        resp = _client(_user("member7")).get(url)
        assert resp.status_code == 200
        assert resp.data["is_serialized"] is True
        assert resp.data["serial_tracking_mode"] == InventoryItem.SERIAL_TRACKING_REUSABLE

    def test_serial_fields_are_read_only(self):
        """Serialization config is not editable through the item serializer."""
        item = InventoryItemFactory(is_serialized=False)
        url = reverse("inventoryitem-detail", kwargs={"pk": item.pk})
        resp = _client(_user("staff7", is_staff=True)).patch(
            url,
            data={
                "is_serialized": True,
                "serial_tracking_mode": InventoryItem.SERIAL_TRACKING_REUSABLE,
            },
            format="json",
        )
        assert resp.status_code == 200
        item.refresh_from_db()
        assert item.is_serialized is False


@pytest.mark.integration
class TestComponentUsageEventApi:
    def test_events_are_listed_and_filterable(self, consumable_item):
        component = SerializedComponentFactory(item=consumable_item)
        component.apply_action(SerializedComponent.ACTION_RECEIVE)

        url = reverse("component-usage-event-list")
        resp = _client(_user("member3")).get(url, {"component": str(component.id)})
        assert resp.status_code == 200
        assert resp.data["count"] == 1
        assert resp.data["results"][0]["action"] == SerializedComponent.ACTION_RECEIVE

    def test_events_list_without_filter(self, consumable_item):
        component = SerializedComponentFactory(item=consumable_item)
        component.apply_action(SerializedComponent.ACTION_RECEIVE)
        url = reverse("component-usage-event-list")
        resp = _client(_user("member5")).get(url)
        assert resp.status_code == 200
        assert resp.data["count"] >= 1

    def test_filter_events_by_asset(self, consumable_item):
        """?asset= surfaces every serial a given machine has used."""
        asset = AssetFactory()
        other_asset = AssetFactory()

        used_here = SerializedComponentFactory(item=consumable_item)
        used_here.apply_action(SerializedComponent.ACTION_RECEIVE)
        used_here.apply_action(SerializedComponent.ACTION_INSTALL, asset=asset)

        used_elsewhere = SerializedComponentFactory(item=consumable_item)
        used_elsewhere.apply_action(SerializedComponent.ACTION_RECEIVE)
        used_elsewhere.apply_action(SerializedComponent.ACTION_INSTALL, asset=other_asset)

        url = reverse("component-usage-event-list")
        resp = _client(_user("member6")).get(url, {"asset": str(asset.id)})
        assert resp.status_code == 200
        # Only the install event that targeted this asset is returned; the
        # receive events (asset=None) and the other machine's event are excluded.
        assert resp.data["count"] == 1
        assert str(resp.data["results"][0]["component"]) == str(used_here.id)
        assert resp.data["results"][0]["action"] == SerializedComponent.ACTION_INSTALL

    def test_events_are_read_only(self, consumable_item):
        component = SerializedComponentFactory(item=consumable_item)
        url = reverse("component-usage-event-list")
        resp = _client(_user("staff9", is_staff=True)).post(
            url,
            data={"component": str(component.id), "action": "receive"},
            format="json",
        )
        assert resp.status_code == 405
        assert ComponentUsageEvent.objects.count() == 0
