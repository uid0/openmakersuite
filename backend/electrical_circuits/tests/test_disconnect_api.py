"""
API tests for the DisconnectViewSet (PR 2/5 of disconnect-promotion).

Covers the staff-only CRUD surface, the nested panel/breaker/circuit
denormalization on read, LOTODevice round-tripping via the dedicated
``required_loto_device_ids`` write field, and the list filter params
(``circuit``, ``location``, ``disconnect_type``, ``needs_review``).
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse

import pytest
from rest_framework.test import APIClient

from electrical_circuits.models import Disconnect, PowerBreaker, PowerCircuit, PowerPanel
from inventory.tests.factories import LocationFactory
from loto.models import LOTODevice

User = get_user_model()


@pytest.fixture
def staff_client(db):
    user = User.objects.create_user(
        username="disc-staff", email="disc-staff@example.com", password="pw", is_staff=True
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def member_client(db):
    user = User.objects.create_user(
        username="disc-member",
        email="disc-member@example.com",
        password="pw",
        is_staff=False,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def loc(db):
    return LocationFactory(name="Disconnect Room")


@pytest.fixture
def circuit(db, loc):
    panel = PowerPanel.objects.create(location=loc, name="Disc panel")
    breaker = PowerBreaker.objects.create(panel=panel, position="7", amperage=40)
    return PowerCircuit.objects.create(breaker=breaker, label="dust collector")


@pytest.fixture
def disconnect(db, circuit, loc):
    return Disconnect.objects.create(
        circuit=circuit,
        location=loc,
        label="Dust collector disconnect",
        disconnect_type=Disconnect.DISCONNECT_TYPE_UNFUSED,
        amperage=40,
    )


# ---------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------


def test_list_rejects_anonymous(api_client):
    assert api_client.get(reverse("disconnect-list")).status_code == 401


def test_list_rejects_non_staff(member_client):
    assert member_client.get(reverse("disconnect-list")).status_code == 403


def test_create_rejects_non_staff(member_client, circuit, loc):
    resp = member_client.post(
        reverse("disconnect-list"),
        {
            "circuit": circuit.pk,
            "location": loc.pk,
            "label": "x",
            "disconnect_type": Disconnect.DISCONNECT_TYPE_TOGGLE,
        },
        format="json",
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------
# Read shape — nested denormalization
# ---------------------------------------------------------------------


def test_retrieve_includes_denormalized_chain(staff_client, disconnect):
    resp = staff_client.get(reverse("disconnect-detail", args=[disconnect.pk]))
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["circuit"] == disconnect.circuit_id
    assert body["circuit_label"] == disconnect.circuit.label
    assert body["breaker_position"] == disconnect.circuit.breaker.position
    assert body["panel_name"] == disconnect.circuit.breaker.panel.name
    assert body["location_name"] == disconnect.location.name
    assert body["required_loto_devices"] == []


def test_retrieve_with_loto_devices_surfaces_nested_payload(staff_client, disconnect, loc):
    device = LOTODevice.objects.create(
        device_type=LOTODevice.DEVICE_PADLOCK,
        label="Red padlock 1",
        location=loc,
    )
    disconnect.required_loto_devices.add(device)
    resp = staff_client.get(reverse("disconnect-detail", args=[disconnect.pk]))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["required_loto_devices"]) == 1
    payload = body["required_loto_devices"][0]
    assert payload["id"] == device.pk
    assert payload["label"] == "Red padlock 1"


# ---------------------------------------------------------------------
# Write paths
# ---------------------------------------------------------------------


def test_create_round_trips(staff_client, circuit, loc):
    resp = staff_client.post(
        reverse("disconnect-list"),
        {
            "circuit": circuit.pk,
            "location": loc.pk,
            "label": "Welder disconnect",
            "disconnect_type": Disconnect.DISCONNECT_TYPE_FUSED,
            "fuse_size": "30A class J",
            "amperage": 30,
        },
        format="json",
    )
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["label"] == "Welder disconnect"
    assert body["disconnect_type"] == "fused"
    assert body["fuse_size"] == "30A class J"
    assert Disconnect.objects.filter(label="Welder disconnect").exists()


def test_create_with_loto_devices(staff_client, circuit, loc):
    d1 = LOTODevice.objects.create(
        device_type=LOTODevice.DEVICE_PADLOCK, label="lock-1", location=loc
    )
    d2 = LOTODevice.objects.create(
        device_type=LOTODevice.DEVICE_PADLOCK, label="lock-2", location=loc
    )
    resp = staff_client.post(
        reverse("disconnect-list"),
        {
            "circuit": circuit.pk,
            "location": loc.pk,
            "label": "Welder disconnect",
            "disconnect_type": Disconnect.DISCONNECT_TYPE_UNFUSED,
            "required_loto_device_ids": [d1.pk, d2.pk],
        },
        format="json",
    )
    assert resp.status_code == 201, resp.content
    created = Disconnect.objects.get(label="Welder disconnect")
    assert set(created.required_loto_devices.values_list("pk", flat=True)) == {d1.pk, d2.pk}


def test_create_with_none_type_allows_blank_location(staff_client, circuit):
    """``disconnect_type=none`` means the breaker serves as the disconnect — no
    physical switch on the wall, so the operator can omit ``location``."""
    resp = staff_client.post(
        reverse("disconnect-list"),
        {
            "circuit": circuit.pk,
            "label": "Inline / breaker-only",
            "disconnect_type": Disconnect.DISCONNECT_TYPE_NONE,
            "is_lockable": False,
        },
        format="json",
    )
    assert resp.status_code == 201, resp.content
    assert Disconnect.objects.get(label="Inline / breaker-only").location_id is None


def test_patch_updates_fields(staff_client, disconnect):
    resp = staff_client.patch(
        reverse("disconnect-detail", args=[disconnect.pk]),
        {"label": "Renamed"},
        format="json",
    )
    assert resp.status_code == 200, resp.content
    disconnect.refresh_from_db()
    assert disconnect.label == "Renamed"


def test_delete_works(staff_client, disconnect):
    resp = staff_client.delete(reverse("disconnect-detail", args=[disconnect.pk]))
    assert resp.status_code == 204
    assert not Disconnect.objects.filter(pk=disconnect.pk).exists()


# ---------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------


def test_filter_by_circuit(staff_client, circuit, loc):
    panel_b = PowerPanel.objects.create(location=loc, name="Panel B")
    breaker_b = PowerBreaker.objects.create(panel=panel_b, position="1", amperage=20)
    circuit_b = PowerCircuit.objects.create(breaker=breaker_b)
    Disconnect.objects.create(
        circuit=circuit,
        location=loc,
        label="on-A",
        disconnect_type=Disconnect.DISCONNECT_TYPE_TOGGLE,
    )
    Disconnect.objects.create(
        circuit=circuit_b,
        location=loc,
        label="on-B",
        disconnect_type=Disconnect.DISCONNECT_TYPE_TOGGLE,
    )
    resp = staff_client.get(reverse("disconnect-list"), {"circuit": circuit.pk})
    assert resp.status_code == 200
    rows = resp.json()["results"]
    assert {row["label"] for row in rows} == {"on-A"}


def test_filter_by_location(staff_client, circuit):
    loc_a = LocationFactory(name="LocA")
    loc_b = LocationFactory(name="LocB")
    Disconnect.objects.create(
        circuit=circuit,
        location=loc_a,
        label="in-A",
        disconnect_type=Disconnect.DISCONNECT_TYPE_TOGGLE,
    )
    Disconnect.objects.create(
        circuit=circuit,
        location=loc_b,
        label="in-B",
        disconnect_type=Disconnect.DISCONNECT_TYPE_TOGGLE,
    )
    resp = staff_client.get(reverse("disconnect-list"), {"location": loc_a.pk})
    assert resp.status_code == 200
    rows = resp.json()["results"]
    assert {row["label"] for row in rows} == {"in-A"}


def test_filter_by_disconnect_type(staff_client, circuit, loc):
    Disconnect.objects.create(
        circuit=circuit,
        location=loc,
        label="fused-1",
        disconnect_type=Disconnect.DISCONNECT_TYPE_FUSED,
        fuse_size="30A",
    )
    Disconnect.objects.create(
        circuit=circuit,
        location=loc,
        label="toggle-1",
        disconnect_type=Disconnect.DISCONNECT_TYPE_TOGGLE,
    )
    resp = staff_client.get(reverse("disconnect-list"), {"disconnect_type": "fused"})
    assert resp.status_code == 200
    rows = resp.json()["results"]
    assert {row["label"] for row in rows} == {"fused-1"}


def test_filter_by_needs_review(staff_client, circuit, loc):
    Disconnect.objects.create(
        circuit=circuit,
        location=loc,
        label="ok",
        disconnect_type=Disconnect.DISCONNECT_TYPE_TOGGLE,
    )
    Disconnect.objects.create(
        circuit=circuit,
        location=loc,
        label="flagged",
        disconnect_type=Disconnect.DISCONNECT_TYPE_TOGGLE,
        needs_review=True,
    )
    resp = staff_client.get(reverse("disconnect-list"), {"needs_review": "true"})
    assert resp.status_code == 200
    rows = resp.json()["results"]
    assert {row["label"] for row in rows} == {"flagged"}
