"""
CRUD tests for the power-topology write API.

The read-only safety API (test_safety_api.py) covers retrieval; this
suite covers the staff-gated POST / PATCH / DELETE surface that backs
the new frontend form pages.
"""

from __future__ import annotations

import itertools

from django.contrib.auth import get_user_model
from django.urls import reverse

import pytest
from rest_framework.test import APIClient

from electrical_circuits.models import PowerBreaker, PowerCircuit, PowerOutlet, PowerPanel
from inventory.tests.factories import LocationFactory

_tag_counter = itertools.count(1)
User = get_user_model()


@pytest.fixture
def staff_client(db):
    user = User.objects.create_user(
        username="crud-staff", email="crud@example.com", password="pw", is_staff=True
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def non_staff_client(db):
    user = User.objects.create_user(
        username="crud-member",
        email="member@example.com",
        password="pw",
        is_staff=False,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def loc(db):
    return LocationFactory(name="CRUD Room")


# ---------------------------------------------------------------------
# Permissions: every CRUD viewset must reject anonymous + non-staff.
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "url_name",
    [
        "powerpanel-list",
        "powerbreaker-list",
        "powercircuit-list",
        "poweroutlet-list",
    ],
)
def test_crud_list_rejects_anonymous(api_client, url_name):
    assert api_client.get(reverse(url_name)).status_code == 401


@pytest.mark.parametrize(
    "url_name",
    [
        "powerpanel-list",
        "powerbreaker-list",
        "powercircuit-list",
        "poweroutlet-list",
    ],
)
def test_crud_list_rejects_non_staff(non_staff_client, url_name):
    assert non_staff_client.get(reverse(url_name)).status_code == 403


# ---------------------------------------------------------------------
# PowerPanel
# ---------------------------------------------------------------------


def test_create_power_panel(staff_client, loc):
    resp = staff_client.post(
        reverse("powerpanel-list"),
        {
            "location": loc.pk,
            "name": "Sewing A",
            "phase_configuration": "split",
            "voltage": 240,
            "main_breaker_amperage": 100,
        },
        format="json",
    )
    assert resp.status_code == 201, resp.content
    panel = PowerPanel.objects.get(name="Sewing A")
    assert panel.location_id == loc.pk
    assert panel.voltage == 240
    # Annotation isn't applied on POST response — falls back to live count.
    assert resp.json()["breaker_count"] == 0


def test_list_power_panels_includes_annotated_breaker_count(staff_client, loc):
    panel = PowerPanel.objects.create(location=loc, name="Sewing A")
    PowerBreaker.objects.create(panel=panel, position="1", amperage=20)
    PowerBreaker.objects.create(panel=panel, position="2", amperage=20)
    resp = staff_client.get(reverse("powerpanel-list"))
    assert resp.status_code == 200
    row = next(p for p in resp.json()["results"] if p["id"] == panel.pk)
    assert row["breaker_count"] == 2
    assert row["location_name"] == loc.name


def test_update_power_panel(staff_client, loc):
    panel = PowerPanel.objects.create(location=loc, name="Old name", voltage=120)
    resp = staff_client.patch(
        reverse("powerpanel-detail", args=[panel.pk]),
        {"name": "New name", "voltage": 240},
        format="json",
    )
    assert resp.status_code == 200, resp.content
    panel.refresh_from_db()
    assert panel.name == "New name"
    assert panel.voltage == 240


def test_delete_power_panel(staff_client, loc):
    panel = PowerPanel.objects.create(location=loc, name="To go")
    resp = staff_client.delete(reverse("powerpanel-detail", args=[panel.pk]))
    assert resp.status_code == 204
    assert not PowerPanel.objects.filter(pk=panel.pk).exists()


# ---------------------------------------------------------------------
# PowerBreaker
# ---------------------------------------------------------------------


def test_create_power_breaker(staff_client, loc):
    panel = PowerPanel.objects.create(location=loc, name="Panel A")
    resp = staff_client.post(
        reverse("powerbreaker-list"),
        {"panel": panel.pk, "position": "12", "amperage": 20, "phase": "A"},
        format="json",
    )
    assert resp.status_code == 201, resp.content
    breaker = PowerBreaker.objects.get(panel=panel, position="12")
    assert breaker.amperage == 20


def test_list_power_breakers_filtered_by_panel(staff_client, loc):
    panel_a = PowerPanel.objects.create(location=loc, name="A")
    panel_b = PowerPanel.objects.create(location=loc, name="B")
    PowerBreaker.objects.create(panel=panel_a, position="1", amperage=20)
    PowerBreaker.objects.create(panel=panel_b, position="2", amperage=20)
    resp = staff_client.get(reverse("powerbreaker-list"), {"panel": panel_a.pk})
    assert resp.status_code == 200
    rows = resp.json()["results"]
    assert len(rows) == 1
    assert rows[0]["panel"] == panel_a.pk


def test_unique_position_violation_returns_400(staff_client, loc):
    panel = PowerPanel.objects.create(location=loc, name="Panel A")
    PowerBreaker.objects.create(panel=panel, position="12", amperage=20)
    resp = staff_client.post(
        reverse("powerbreaker-list"),
        {"panel": panel.pk, "position": "12", "amperage": 20},
        format="json",
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------
# PowerCircuit
# ---------------------------------------------------------------------


def test_create_power_circuit_defaults_max_load_to_80pct(staff_client, loc):
    panel = PowerPanel.objects.create(location=loc, name="Panel A")
    breaker = PowerBreaker.objects.create(panel=panel, position="3", amperage=30)
    resp = staff_client.post(
        reverse("powercircuit-list"),
        {"breaker": breaker.pk, "label": "Bench row 1"},
        format="json",
    )
    assert resp.status_code == 201, resp.content
    circuit = PowerCircuit.objects.get(breaker=breaker, label="Bench row 1")
    # NEC 80% derate from save()
    assert circuit.max_load_amps == 24


def test_list_power_circuits_filtered_by_breaker(staff_client, loc):
    panel = PowerPanel.objects.create(location=loc, name="P")
    b1 = PowerBreaker.objects.create(panel=panel, position="1", amperage=20)
    b2 = PowerBreaker.objects.create(panel=panel, position="2", amperage=20)
    PowerCircuit.objects.create(breaker=b1, label="C-1")
    PowerCircuit.objects.create(breaker=b2, label="C-2")
    resp = staff_client.get(reverse("powercircuit-list"), {"breaker": b1.pk})
    assert resp.status_code == 200
    rows = resp.json()["results"]
    assert len(rows) == 1
    assert rows[0]["breaker"] == b1.pk


# ---------------------------------------------------------------------
# PowerOutlet
# ---------------------------------------------------------------------


def test_create_power_outlet(staff_client, loc):
    panel = PowerPanel.objects.create(location=loc, name="P")
    breaker = PowerBreaker.objects.create(panel=panel, position="1", amperage=20)
    circuit = PowerCircuit.objects.create(breaker=breaker, label="C")
    resp = staff_client.post(
        reverse("poweroutlet-list"),
        {
            "circuit": circuit.pk,
            "location": loc.pk,
            "label": "bench-1",
            "outlet_type": "5-20R",
        },
        format="json",
    )
    assert resp.status_code == 201, resp.content
    outlet = PowerOutlet.objects.get(label="bench-1")
    assert outlet.circuit_id == circuit.pk
    assert outlet.outlet_type == "5-20R"


def test_list_power_outlets_filtered_by_circuit(staff_client, loc):
    panel = PowerPanel.objects.create(location=loc, name="P")
    breaker = PowerBreaker.objects.create(panel=panel, position="1", amperage=20)
    c1 = PowerCircuit.objects.create(breaker=breaker, label="C-1")
    c2 = PowerCircuit.objects.create(breaker=breaker, label="C-2")
    PowerOutlet.objects.create(circuit=c1, location=loc, label="a")
    PowerOutlet.objects.create(circuit=c2, location=loc, label="b")
    resp = staff_client.get(reverse("poweroutlet-list"), {"circuit": c1.pk})
    assert resp.status_code == 200
    rows = resp.json()["results"]
    assert len(rows) == 1
    assert rows[0]["circuit"] == c1.pk


# PowerPort + PowerCable CRUD endpoints were removed when the cable / cordset
# abstraction was retired (operators routinely failed to keep the data
# current). Assets now point at their feeding breaker directly via
# ``Asset.breaker`` — exercised by the inventory app's CRUD tests.
