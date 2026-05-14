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


# ---------------------------------------------------------------------------
# PowerPort + PowerCable CRUD — backs the asset-side power-chain edit UI.
# ---------------------------------------------------------------------------


@pytest.fixture
def asset(db):
    from inventory.tests.factories import AssetFactory

    return AssetFactory(name="Lathe", asset_tag="A-PC1")


@pytest.fixture
def power_outlet(db, loc):
    panel = PowerPanel.objects.create(name="P1", location=loc)
    breaker = PowerBreaker.objects.create(panel=panel, position="A-01", amperage=20)
    circuit = PowerCircuit.objects.create(breaker=breaker, label="C1")
    return PowerOutlet.objects.create(circuit=circuit, location=loc, label="O1")


@pytest.mark.django_db
class TestPowerPortCRUD:
    def test_staff_can_create_port(self, staff_client, asset):
        url = reverse("powerport-list")
        resp = staff_client.post(
            url,
            {"asset": str(asset.id), "label": "Main", "port_type": "5-15R"},
            format="json",
        )
        assert resp.status_code == 201, resp.data
        assert str(resp.data["asset"]) == str(asset.id)
        assert resp.data["label"] == "Main"
        assert resp.data["asset_name"] == asset.name

    def test_filter_by_asset(self, staff_client, asset):
        from electrical_circuits.models import PowerPort
        from inventory.tests.factories import AssetFactory

        PowerPort.objects.create(asset=asset, label="Main", port_type="5-15R")
        other = AssetFactory(name="Other")
        PowerPort.objects.create(asset=other, label="Main", port_type="5-15R")

        resp = staff_client.get(reverse("powerport-list"), {"asset": str(asset.id)})
        assert resp.status_code == 200
        rows = resp.data["results"] if isinstance(resp.data, dict) else resp.data
        assert len(rows) == 1
        assert str(rows[0]["asset"]) == str(asset.id)

    def test_non_staff_cannot_create(self, non_staff_client, asset):
        resp = non_staff_client.post(
            reverse("powerport-list"),
            {"asset": str(asset.id), "label": "Main", "port_type": "5-15R"},
            format="json",
        )
        assert resp.status_code == 403

    def test_unique_label_per_asset(self, staff_client, asset):
        from electrical_circuits.models import PowerPort

        PowerPort.objects.create(asset=asset, label="Main", port_type="5-15R")
        resp = staff_client.post(
            reverse("powerport-list"),
            {"asset": str(asset.id), "label": "Main", "port_type": "5-15R"},
            format="json",
        )
        assert resp.status_code == 400


@pytest.mark.django_db
class TestPowerCableCRUD:
    def _make_port(self, asset, label="Main"):
        from electrical_circuits.models import PowerPort

        return PowerPort.objects.create(asset=asset, label=label, port_type="5-15R")

    def test_create_power_cable_via_flat_fields(self, staff_client, asset, power_outlet):
        port = self._make_port(asset)
        resp = staff_client.post(
            reverse("powercable-list"),
            {
                "outlet": power_outlet.pk,
                "port": port.pk,
                "status": "connected",
                "length_ft": 6,
            },
            format="json",
        )
        assert resp.status_code == 201, resp.data
        assert resp.data["outlet_id"] == power_outlet.pk
        assert resp.data["port_id"] == port.pk
        assert str(resp.data["asset_id"]) == str(asset.id)
        assert resp.data["asset_name"] == asset.name

    def test_filter_by_asset_returns_only_that_assets_cables(
        self, staff_client, asset, power_outlet
    ):
        from inventory.tests.factories import AssetFactory

        port_a = self._make_port(asset, "Main")
        staff_client.post(
            reverse("powercable-list"),
            {"outlet": power_outlet.pk, "port": port_a.pk},
            format="json",
        )

        other = AssetFactory(name="Other")
        port_b = self._make_port(other, "Main")
        staff_client.post(
            reverse("powercable-list"),
            {"outlet": power_outlet.pk, "port": port_b.pk},
            format="json",
        )

        resp = staff_client.get(reverse("powercable-list"), {"asset": str(asset.id)})
        assert resp.status_code == 200
        rows = resp.data["results"] if isinstance(resp.data, dict) else resp.data
        assert len(rows) == 1
        assert str(rows[0]["asset_id"]) == str(asset.id)

    def test_non_staff_cannot_create(self, non_staff_client, asset, power_outlet):
        port = self._make_port(asset)
        resp = non_staff_client.post(
            reverse("powercable-list"),
            {"outlet": power_outlet.pk, "port": port.pk},
            format="json",
        )
        assert resp.status_code == 403

    def test_delete_unwires_chain(self, staff_client, asset, power_outlet):
        port = self._make_port(asset)
        created = staff_client.post(
            reverse("powercable-list"),
            {"outlet": power_outlet.pk, "port": port.pk},
            format="json",
        )
        cable_id = created.data["id"]
        resp = staff_client.delete(reverse("powercable-detail", args=[cable_id]))
        assert resp.status_code == 204
        list_resp = staff_client.get(reverse("powercable-list"), {"asset": str(asset.id)})
        rows = list_resp.data["results"] if isinstance(list_resp.data, dict) else list_resp.data
        assert rows == []
