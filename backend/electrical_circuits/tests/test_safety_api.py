"""
Tests for the safety query API.

The cable/cordset model was removed in favour of a direct ``Asset.breaker``
FK. These tests cover the same endpoints but seed the new graph and adjust
the expected shape (no more ``cable`` / ``port`` hops, draw estimated from
``Asset.power_draw_watts`` over the panel voltage).
"""

from __future__ import annotations

import itertools
import time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse

import pytest
from rest_framework.test import APIClient

from electrical_circuits.models import PowerBreaker, PowerCircuit, PowerOutlet, PowerPanel
from inventory.tests.factories import AssetFactory, LocationFactory

_tag_counter = itertools.count(1)
User = get_user_model()


def _make_asset(name: str | None = None, **kwargs):
    kwargs.setdefault("asset_tag", f"B25-{next(_tag_counter):06d}")
    if name is not None:
        kwargs["name"] = name
    return AssetFactory(**kwargs)


@pytest.fixture
def staff_client(db):
    user = User.objects.create_user(
        username="staffuser", email="staff@example.com", password="pw", is_staff=True
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def non_staff_client(db):
    user = User.objects.create_user(
        username="regular", email="regular@example.com", password="pw", is_staff=False
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def topology(db):
    """A small topology with two assets — one critical, one not — both
    assigned to the same breaker on one panel.
    """

    loc = LocationFactory(name="Sewing Room")
    panel = PowerPanel.objects.create(location=loc, name="Sewing A", voltage=120)
    breaker = PowerBreaker.objects.create(panel=panel, position="2", amperage=20)
    circuit = PowerCircuit.objects.create(breaker=breaker, label="Bench row 1")

    outlet1 = PowerOutlet.objects.create(circuit=circuit, location=loc, label="bench-1")
    outlet2 = PowerOutlet.objects.create(circuit=circuit, location=loc, label="bench-2")

    asset_critical = _make_asset(
        name="Server",
        location=loc,
        is_critical=True,
        breaker=breaker,
        power_draw_watts=Decimal("480.0"),
    )
    asset_normal = _make_asset(
        name="Lamp",
        location=loc,
        breaker=breaker,
        power_draw_watts=Decimal("60.0"),
    )

    return {
        "loc": loc,
        "panel": panel,
        "breaker": breaker,
        "circuit": circuit,
        "outlet1": outlet1,
        "outlet2": outlet2,
        "asset_critical": asset_critical,
        "asset_normal": asset_normal,
    }


# ---------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------


def test_trip_impact_requires_authentication(api_client, topology):
    url = reverse("electrical-breaker-trip-impact", args=[topology["breaker"].pk])
    resp = api_client.get(url)
    assert resp.status_code == 401


def test_trip_impact_rejects_non_staff(non_staff_client, topology):
    url = reverse("electrical-breaker-trip-impact", args=[topology["breaker"].pk])
    resp = non_staff_client.get(url)
    assert resp.status_code == 403


def test_circuit_load_requires_staff(non_staff_client, topology):
    url = reverse("electrical-circuit-load", args=[topology["circuit"].pk])
    assert non_staff_client.get(url).status_code == 403


def test_panel_topology_requires_staff(non_staff_client, topology):
    url = reverse("electrical-panel-topology", args=[topology["panel"].pk])
    assert non_staff_client.get(url).status_code == 403


def test_asset_power_chain_requires_staff(non_staff_client, topology):
    url = reverse("asset-power-chain", args=[topology["asset_critical"].pk])
    assert non_staff_client.get(url).status_code == 403


def test_panel_list_requires_staff(non_staff_client, topology):
    url = reverse("electrical-panel-list")
    assert non_staff_client.get(url).status_code == 403


# ---------------------------------------------------------------------
# Panel list
# ---------------------------------------------------------------------


def test_panel_list_returns_breaker_count_and_review_flag(staff_client, topology, db):
    placeholder = PowerPanel.objects.create(
        location=topology["loc"], name="Placeholder", needs_review=True
    )

    resp = staff_client.get(reverse("electrical-panel-list"))
    assert resp.status_code == 200
    by_name = {p["name"]: p for p in resp.json()["results"]}
    assert by_name["Sewing A"]["breaker_count"] == 1
    assert by_name["Sewing A"]["needs_review"] is False
    assert by_name["Placeholder"]["breaker_count"] == 0
    assert by_name["Placeholder"]["needs_review"] is True
    assert by_name["Placeholder"]["id"] == placeholder.pk


# ---------------------------------------------------------------------
# Trip impact
# ---------------------------------------------------------------------


def test_trip_impact_returns_assets_and_critical(staff_client, topology):
    url = reverse("electrical-breaker-trip-impact", args=[topology["breaker"].pk])
    resp = staff_client.get(url)
    assert resp.status_code == 200
    data = resp.json()
    assert data["breaker"]["id"] == topology["breaker"].pk
    asset_names = sorted(a["name"] for a in data["assets"])
    assert asset_names == ["Lamp", "Server"]
    critical_names = [a["name"] for a in data["critical_loads"]]
    assert critical_names == ["Server"]


def test_trip_impact_unknown_breaker_returns_404(staff_client, db):
    url = reverse("electrical-breaker-trip-impact", args=[99999])
    assert staff_client.get(url).status_code == 404


# ---------------------------------------------------------------------
# Circuit load
# ---------------------------------------------------------------------


def test_circuit_load_returns_total_draw_and_utilization(staff_client, topology):
    url = reverse("electrical-circuit-load", args=[topology["circuit"].pk])
    resp = staff_client.get(url)
    assert resp.status_code == 200
    data = resp.json()
    assert data["connected_device_count"] == 2
    # 480W + 60W = 540W. At 120V → 4.5A.
    assert data["estimated_max_draw_amps"] == pytest.approx(4.5)
    # capacity defaults to 0.8 * 20 = 16
    assert data["capacity_amps"] == 16
    # 4.5 / 16 * 100 = 28.125%
    assert data["capacity_utilization_percent"] == pytest.approx(28.13, abs=0.05)


def test_circuit_load_handles_no_capacity(staff_client, db):
    loc = LocationFactory()
    panel = PowerPanel.objects.create(location=loc, name="P", voltage=120)
    breaker = PowerBreaker.objects.create(panel=panel, position="1", amperage=20)
    circuit = PowerCircuit.objects.create(breaker=breaker)
    PowerCircuit.objects.filter(pk=circuit.pk).update(max_load_amps=None)

    url = reverse("electrical-circuit-load", args=[circuit.pk])
    data = staff_client.get(url).json()
    assert data["capacity_amps"] is None
    assert data["capacity_utilization_percent"] is None


# ---------------------------------------------------------------------
# Panel topology
# ---------------------------------------------------------------------


def test_panel_topology_returns_full_tree(staff_client, topology):
    url = reverse("electrical-panel-topology", args=[topology["panel"].pk])
    resp = staff_client.get(url)
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == topology["panel"].pk
    assert len(data["breakers"]) == 1
    breaker = data["breakers"][0]
    assert breaker["id"] == topology["breaker"].pk
    assert len(breaker["circuits"]) == 1
    circuit = breaker["circuits"][0]
    assert circuit["id"] == topology["circuit"].pk
    outlet_labels = sorted(o["label"] for o in circuit["outlets"])
    assert outlet_labels == ["bench-1", "bench-2"]


def test_panel_topology_surfaces_assets_on_breaker_per_outlet(staff_client, topology, db):
    """Without cables there is no outlet ↔ asset edge; every outlet on the
    breaker reports every asset assigned to the breaker.
    """

    PowerOutlet.objects.create(
        circuit=topology["circuit"], location=topology["loc"], label="bench-bare"
    )

    url = reverse("electrical-panel-topology", args=[topology["panel"].pk])
    resp = staff_client.get(url)
    assert resp.status_code == 200
    outlets = {o["label"]: o for o in resp.json()["breakers"][0]["circuits"][0]["outlets"]}

    # Every outlet on the breaker surfaces both assets.
    for label in ("bench-1", "bench-2", "bench-bare"):
        names = sorted(a["name"] for a in outlets[label]["connected_assets"])
        assert names == ["Lamp", "Server"]


def test_panel_topology_exposes_subpanel_hierarchy(staff_client, topology):
    loc = LocationFactory(name="Machine Shop")
    subpanel = PowerPanel.objects.create(
        location=loc,
        name="LV4",
        fed_by=topology["circuit"],
    )

    sub_url = reverse("electrical-panel-topology", args=[subpanel.pk])
    sub_data = staff_client.get(sub_url).json()
    assert sub_data["fed_by_summary"] is not None
    assert sub_data["fed_by_summary"]["panel_id"] == topology["panel"].pk
    assert sub_data["fed_by_summary"]["breaker_id"] == topology["breaker"].pk
    assert sub_data["fed_by_summary"]["circuit_id"] == topology["circuit"].pk
    assert sub_data["downstream_panels"] == []

    parent_url = reverse("electrical-panel-topology", args=[topology["panel"].pk])
    parent_data = staff_client.get(parent_url).json()
    assert parent_data["fed_by_summary"] is None
    assert {"id": subpanel.pk, "name": "LV4"} in parent_data["downstream_panels"]


# ---------------------------------------------------------------------
# Asset power chain
# ---------------------------------------------------------------------


def test_asset_power_chain_returns_full_chain(staff_client, topology):
    url = reverse("asset-power-chain", args=[topology["asset_critical"].pk])
    resp = staff_client.get(url)
    assert resp.status_code == 200
    data = resp.json()
    kinds = [hop["kind"] for hop in data["chain"]]
    assert kinds == ["panel", "breaker", "circuit"]
    assert data["asset"]["id"] == str(topology["asset_critical"].pk)


def test_asset_power_chain_empty_for_unconnected(staff_client, db):
    asset = _make_asset(name="Loose")
    url = reverse("asset-power-chain", args=[asset.pk])
    data = staff_client.get(url).json()
    assert data["chain"] == []


# ---------------------------------------------------------------------
# Perf budget
# ---------------------------------------------------------------------


@pytest.mark.django_db
def test_panel_topology_under_two_seconds_at_scale(staff_client):
    """1 site, 10 panels, 200 circuits, 200 devices — well under 2s."""

    loc = LocationFactory(name="Site")
    target_panel = None
    devices_made = 0
    circuits_made = 0
    for p_idx in range(10):
        panel = PowerPanel.objects.create(location=loc, name=f"Panel-{p_idx}", voltage=120)
        if p_idx == 0:
            target_panel = panel
        for c_idx in range(20):
            breaker = PowerBreaker.objects.create(panel=panel, position=str(c_idx + 1), amperage=20)
            circuit = PowerCircuit.objects.create(breaker=breaker)
            PowerOutlet.objects.create(circuit=circuit, location=loc, label=f"p{p_idx}-c{c_idx}")
            circuits_made += 1
            if devices_made < 500:
                _make_asset(
                    name=f"Dev-{devices_made}",
                    breaker=breaker,
                    power_draw_watts=Decimal("120.0"),
                )
                devices_made += 1

    assert circuits_made == 200
    assert devices_made == 200

    url = reverse("electrical-panel-topology", args=[target_panel.pk])
    staff_client.get(url)  # warm caches
    start = time.perf_counter()
    resp = staff_client.get(url)
    elapsed = time.perf_counter() - start
    assert resp.status_code == 200
    assert elapsed < 2.0, f"panel topology took {elapsed:.2f}s (>2s budget)"
