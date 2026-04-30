"""API-level tests for electrical_circuits (oms-tt5)."""

from django.urls import reverse

import pytest

from electrical_circuits.models import Breaker, LightSwitch, NetworkDrop, Outlet
from inventory.tests.factories import LocationFactory


@pytest.fixture
def panel_room(db):
    return LocationFactory()


@pytest.fixture
def shop(db):
    return LocationFactory()


@pytest.fixture
def breaker(panel_room):
    return Breaker.objects.create(location=panel_room, panel="A", breaker_number="12", amperage=20)


def test_breaker_list_requires_auth(api_client):
    url = reverse("breaker-list")
    response = api_client.get(url)
    assert response.status_code == 401


def test_breaker_create_via_api(authenticated_client, panel_room):
    client, _ = authenticated_client
    url = reverse("breaker-list")
    response = client.post(
        url,
        {
            "location": panel_room.pk,
            "panel": "Panel B",
            "breaker_number": "8",
            "amperage": 30,
            "voltage": 240,
            "poles": 2,
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    assert Breaker.objects.filter(panel="Panel B", breaker_number="8").exists()


def test_outlet_create_and_list_filter_by_location(authenticated_client, breaker, shop, panel_room):
    client, _ = authenticated_client
    url = reverse("outlet-list")
    Outlet.objects.create(
        location=shop, identifier="bench-1", breaker=breaker, outlet_type="nema_5_20"
    )
    Outlet.objects.create(location=panel_room, identifier="panel-test")

    resp = client.get(url, {"location": shop.pk})
    assert resp.status_code == 200
    data = resp.json()
    results = data.get("results", data)
    assert len(results) == 1
    assert results[0]["identifier"] == "bench-1"
    assert results[0]["breaker_label"] == "A / 12"
    assert results[0]["location_name"] == shop.name


def test_outlet_filter_by_breaker(authenticated_client, breaker, shop):
    client, _ = authenticated_client
    Outlet.objects.create(location=shop, identifier="o1", breaker=breaker)
    Outlet.objects.create(location=shop, identifier="o2")

    resp = client.get(reverse("outlet-list"), {"breaker": breaker.pk})
    results = resp.json().get("results", resp.json())
    idents = sorted(r["identifier"] for r in results)
    assert idents == ["o1"]


def test_lightswitch_filter_by_controls_location(authenticated_client, shop):
    client, _ = authenticated_client
    hallway = LocationFactory()
    LightSwitch.objects.create(location=hallway, identifier="east-door", controls_location=shop)
    LightSwitch.objects.create(location=hallway, identifier="north-door")

    resp = client.get(reverse("light-switch-list"), {"controls_location": shop.pk})
    results = resp.json().get("results", resp.json())
    assert len(results) == 1
    assert results[0]["controls_location_name"] == shop.name


def test_networkdrop_filter_by_drop_type(authenticated_client):
    client, _ = authenticated_client
    closet = LocationFactory()
    NetworkDrop.objects.create(location=closet, identifier="rack-1-1", drop_type="patch_panel")
    NetworkDrop.objects.create(location=closet, identifier="ap-1", drop_type="ap")

    resp = client.get(reverse("network-drop-list"), {"drop_type": "ap"})
    results = resp.json().get("results", resp.json())
    assert len(results) == 1
    assert results[0]["identifier"] == "ap-1"


def test_outlet_unique_identifier_constraint_returns_400(authenticated_client, shop):
    client, _ = authenticated_client
    Outlet.objects.create(location=shop, identifier="bench-1")
    resp = client.post(
        reverse("outlet-list"),
        {"location": shop.pk, "identifier": "bench-1"},
        format="json",
    )
    assert resp.status_code == 400
