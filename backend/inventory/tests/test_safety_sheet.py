"""Tests for ``/api/inventory/locations/<id>/safety-sheet/`` (oms-gzycmj).

Each test maps to one acceptance line in the issue:

* renders lights / outlets (grouped) / thermostats / all_kill_breakers
* hardwired thermostat resolves through disconnect.circuit.breaker
* missing controlled_asset → kill_breakers=[] and needs_review=True
* non-staff → 403
"""

from __future__ import annotations

from django.contrib.auth import get_user_model

import pytest
from rest_framework.test import APIClient

from climate.models import Thermostat
from electrical_circuits.models import (
    Breaker,
    Disconnect,
    HardwiredConnection,
    LightSwitch,
    Outlet,
    PowerBreaker,
    PowerCircuit,
    PowerOutlet,
    PowerPanel,
)
from inventory.tests.factories import AssetFactory, LocationFactory

User = get_user_model()


@pytest.fixture
def staff_client(db):
    user = User.objects.create_user(
        username="staffuser", email="staff@example.com", password="pw", is_staff=True
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def member_client(db):
    user = User.objects.create_user(
        username="memberuser", email="m@example.com", password="pw", is_staff=False
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _url(location_id: int) -> str:
    return f"/api/inventory/locations/{location_id}/safety-sheet/"


@pytest.mark.django_db
def test_non_staff_member_is_forbidden(member_client):
    loc = LocationFactory()
    resp = member_client.get(_url(loc.pk))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_anonymous_is_unauthorized(db):
    loc = LocationFactory()
    resp = APIClient().get(_url(loc.pk))
    assert resp.status_code in (401, 403)


@pytest.mark.django_db
def test_unknown_location_returns_404(staff_client):
    resp = staff_client.get(_url(999999))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_safety_sheet_full_shape(staff_client):
    """Sheet pulls lights, outlets (grouped), thermostats, kill list."""

    elec_room = LocationFactory(name="SafetySheet Elec Room")
    wood_shop = LocationFactory(name="SafetySheet Wood Shop")

    # Legacy panel-A breaker 14: lights breaker for the shop
    light_breaker = Breaker.objects.create(
        location=elec_room, panel="A", breaker_number="14", amperage=15
    )
    LightSwitch.objects.create(
        location=wood_shop,
        controls_location=wood_shop,
        identifier="door-east",
        breaker=light_breaker,
    )

    # Five legacy outlets on the same breaker → outlet_count=5, one row
    outlet_breaker = Breaker.objects.create(
        location=elec_room, panel="A", breaker_number="18", amperage=20
    )
    for i in range(5):
        Outlet.objects.create(
            location=wood_shop,
            identifier=f"ne-bench-{i}",
            breaker=outlet_breaker,
        )

    # One legacy outlet on a different breaker → second row
    other_outlet_breaker = Breaker.objects.create(
        location=elec_room, panel="B", breaker_number="6", amperage=30
    )
    Outlet.objects.create(
        location=wood_shop,
        identifier="table-saw",
        breaker=other_outlet_breaker,
    )

    # One PowerOutlet (new topology) in the same room → grouped separately
    power_panel = PowerPanel.objects.create(location=elec_room, name="Panel C")
    power_breaker = PowerBreaker.objects.create(panel=power_panel, position="2", amperage=20)
    power_circuit = PowerCircuit.objects.create(breaker=power_breaker, label="Bench north")
    PowerOutlet.objects.create(circuit=power_circuit, location=wood_shop, label="NE-bench-power")

    # Thermostat with a hardwired asset → kill breaker through Disconnect
    hvac = AssetFactory(name="RTU-3", location=wood_shop)
    hvac_breaker = PowerBreaker.objects.create(panel=power_panel, position="22", amperage=30)
    hvac_circuit = PowerCircuit.objects.create(breaker=hvac_breaker, label="RTU-3 feeder")
    hvac_disconnect = Disconnect.objects.create(
        circuit=hvac_circuit,
        location=wood_shop,
        label="RTU-3 disconnect",
        disconnect_type=Disconnect.DISCONNECT_TYPE_UNFUSED,
    )
    HardwiredConnection.objects.create(asset=hvac, disconnect=hvac_disconnect)
    Thermostat.objects.create(
        location=wood_shop,
        controls_location=wood_shop,
        label="Wood Shop North",
        manufacturer="Honeywell",
        model="T6 Pro",
        controlled_asset=hvac,
    )

    resp = staff_client.get(_url(wood_shop.pk))
    assert resp.status_code == 200, resp.content
    body = resp.json()

    assert body["location"] == {"id": wood_shop.pk, "name": "SafetySheet Wood Shop"}

    # Lights
    assert len(body["lights"]) == 1
    light = body["lights"][0]
    assert light["switch_label"] == "door-east"
    assert light["panel"] == "A"
    assert light["position"] == "14"
    assert light["amperage"] == 15

    # Outlets — three groups: legacy A/18 (5 outlets), legacy B/6 (1),
    # power C/2 (1).
    outlet_groups = body["outlets"]
    keyed = {(g["panel"], g["position"]): g for g in outlet_groups}
    a18 = keyed[("A", "18")]
    assert a18["outlet_count"] == 5
    assert a18["amperage"] == 20
    b6 = keyed[("B", "6")]
    assert b6["outlet_count"] == 1
    c2 = keyed[("Panel C", "2")]
    assert c2["outlet_count"] == 1

    # Thermostat
    assert len(body["thermostats"]) == 1
    t = body["thermostats"][0]
    assert t["label"] == "Wood Shop North"
    assert t["controlled_asset"]["name"] == "RTU-3"
    assert t["needs_review"] is False
    assert len(t["kill_breakers"]) == 1
    kb = t["kill_breakers"][0]
    assert kb["panel"] == "Panel C"
    assert kb["position"] == "22"
    assert kb["amperage"] == 30
    assert kb["source"] == "hardwired"

    # all_kill_breakers — deduped union, sorted
    all_kb = body["all_kill_breakers"]
    keys = {(k["panel"], k["position"]) for k in all_kb}
    assert keys == {("A", "14"), ("A", "18"), ("B", "6"), ("Panel C", "2"), ("Panel C", "22")}


@pytest.mark.django_db
def test_thermostat_without_asset_flags_needs_review(staff_client):
    loc = LocationFactory()
    Thermostat.objects.create(location=loc, controls_location=loc, label="Front office")
    resp = staff_client.get(_url(loc.pk))
    assert resp.status_code == 200
    t = resp.json()["thermostats"][0]
    assert t["controlled_asset"] is None
    assert t["kill_breakers"] == []
    assert t["needs_review"] is True


@pytest.mark.django_db
def test_thermostat_with_asset_but_no_feed_flags_needs_review(staff_client):
    """Asset with no hardwired connection and no cordset → empty kill list."""

    loc = LocationFactory()
    asset = AssetFactory(name="Orphan HVAC", location=loc)
    Thermostat.objects.create(
        location=loc,
        controls_location=loc,
        label="Storage stat",
        controlled_asset=asset,
    )
    resp = staff_client.get(_url(loc.pk))
    assert resp.status_code == 200
    t = resp.json()["thermostats"][0]
    assert t["kill_breakers"] == []
    assert t["needs_review"] is True


@pytest.mark.django_db
def test_outlets_grouped_dedup_when_no_breaker(staff_client):
    """Outlets with no breaker are surfaced as a single 'unassigned' row."""

    loc = LocationFactory()
    Outlet.objects.create(location=loc, identifier="A")
    Outlet.objects.create(location=loc, identifier="B")
    resp = staff_client.get(_url(loc.pk))
    assert resp.status_code == 200
    rows = resp.json()["outlets"]
    assert len(rows) == 1
    assert rows[0]["outlet_count"] == 2
    assert rows[0]["panel"] is None


@pytest.mark.django_db
def test_only_lightswitches_with_controls_location_match(staff_client):
    """Light switches mounted in a hallway but controlling the shop appear."""

    elec = LocationFactory()
    hallway = LocationFactory()
    wood = LocationFactory()
    b = Breaker.objects.create(location=elec, panel="A", breaker_number="14", amperage=15)
    # Switch lives in hallway but controls wood shop
    LightSwitch.objects.create(
        location=hallway, controls_location=wood, identifier="hall-wood", breaker=b
    )
    resp = staff_client.get(_url(wood.pk))
    assert resp.status_code == 200
    lights = resp.json()["lights"]
    assert len(lights) == 1
    assert lights[0]["switch_label"] == "hall-wood"
