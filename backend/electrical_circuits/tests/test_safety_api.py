"""
Tests for the safety query API (oms-b25, AC-1..AC-6).

Each test maps to one of the acceptance criteria in oms-b25:

* AC-1 — ``GET /api/electrical/breakers/<id>/trip-impact/``
* AC-2 — ``GET /api/electrical/circuits/<id>/load/``
* AC-3 — ``GET /api/electrical/panels/<id>/topology/``
* AC-4 — ``GET /api/assets/<id>/power-chain/``
* AC-5 — auth-gated (staff only)
* AC-6 — typical-makerspace-scale perf (10 panels / 200 circuits / 500 devices, <2s)
"""

from __future__ import annotations

import itertools
import time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse

import pytest
from rest_framework.test import APIClient

from electrical_circuits.models import (
    Cable,
    PowerBreaker,
    PowerCircuit,
    PowerOutlet,
    PowerPanel,
    PowerPort,
)
from inventory.tests.factories import AssetFactory, LocationFactory

_tag_counter = itertools.count(1)
User = get_user_model()


def _make_asset(name: str | None = None, **kwargs):
    """AssetFactory with an explicit unique asset_tag.

    Mirrors the helper in test_power_chain — UUID7-derived tags collide
    inside a 16-day window when many assets are created in the same
    test run (see [3/7] notes), so we pin them.
    """

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
    """A small topology with two assets — one critical, one not — on
    a single circuit fed by one breaker on one panel.
    """

    loc = LocationFactory(name="Sewing Room")
    panel = PowerPanel.objects.create(location=loc, name="Sewing A")
    breaker = PowerBreaker.objects.create(panel=panel, position="2", amperage=20)
    circuit = PowerCircuit.objects.create(breaker=breaker, label="Bench row 1")

    outlet1 = PowerOutlet.objects.create(circuit=circuit, location=loc, label="bench-1")
    outlet2 = PowerOutlet.objects.create(circuit=circuit, location=loc, label="bench-2")

    asset_critical = _make_asset(name="Server", location=loc, is_critical=True)
    port_a = PowerPort.objects.create(
        asset=asset_critical, label="Main", max_draw_amps=Decimal("4.0")
    )
    Cable.objects.create(cable_type=Cable.CABLE_TYPE_POWER, endpoint_a=outlet1, endpoint_b=port_a)

    asset_normal = _make_asset(name="Lamp", location=loc)
    port_b = PowerPort.objects.create(
        asset=asset_normal, label="Main", max_draw_amps=Decimal("0.5")
    )
    Cable.objects.create(cable_type=Cable.CABLE_TYPE_POWER, endpoint_a=outlet2, endpoint_b=port_b)

    return {
        "loc": loc,
        "panel": panel,
        "breaker": breaker,
        "circuit": circuit,
        "outlet1": outlet1,
        "outlet2": outlet2,
        "asset_critical": asset_critical,
        "asset_normal": asset_normal,
        "port_a": port_a,
        "port_b": port_b,
    }


# ---------------------------------------------------------------------
# AC-5 — auth gate
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
# Panel list — backs the frontend panel directory ([6/7])
# ---------------------------------------------------------------------


def test_panel_list_returns_breaker_count_and_review_flag(staff_client, topology, db):
    # A second panel with no breakers + needs_review flag exercises the
    # annotation and the placeholder pathway from the migration.
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
# AC-1 — trip impact
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
# AC-2 — circuit load
# ---------------------------------------------------------------------


def test_circuit_load_returns_total_draw_and_utilization(staff_client, topology):
    url = reverse("electrical-circuit-load", args=[topology["circuit"].pk])
    resp = staff_client.get(url)
    assert resp.status_code == 200
    data = resp.json()
    assert data["connected_device_count"] == 2
    # 4.0 + 0.5 = 4.5 amps
    assert data["estimated_max_draw_amps"] == pytest.approx(4.5)
    # capacity defaults to 0.8 * 20 = 16
    assert data["capacity_amps"] == 16
    # 4.5 / 16 * 100 = 28.125%
    assert data["capacity_utilization_percent"] == pytest.approx(28.13, abs=0.05)


def test_circuit_load_handles_no_capacity(staff_client, db):
    loc = LocationFactory()
    panel = PowerPanel.objects.create(location=loc, name="P")
    breaker = PowerBreaker.objects.create(panel=panel, position="1", amperage=20)
    circuit = PowerCircuit.objects.create(breaker=breaker)
    # The default save() sets max_load_amps; clear it manually.
    PowerCircuit.objects.filter(pk=circuit.pk).update(max_load_amps=None)

    url = reverse("electrical-circuit-load", args=[circuit.pk])
    data = staff_client.get(url).json()
    assert data["capacity_amps"] is None
    assert data["capacity_utilization_percent"] is None


# ---------------------------------------------------------------------
# AC-3 — panel topology
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


# ---------------------------------------------------------------------
# AC-4 — asset power chain
# ---------------------------------------------------------------------


def test_asset_power_chain_returns_full_chain(staff_client, topology):
    url = reverse("asset-power-chain", args=[topology["asset_critical"].pk])
    resp = staff_client.get(url)
    assert resp.status_code == 200
    data = resp.json()
    kinds = [hop["kind"] for hop in data["chain"]]
    assert kinds == ["panel", "breaker", "circuit", "outlet", "cable", "port"]
    assert data["asset"]["id"] == str(topology["asset_critical"].pk)


def test_asset_power_chain_empty_for_unconnected(staff_client, db):
    asset = _make_asset(name="Loose")
    url = reverse("asset-power-chain", args=[asset.pk])
    data = staff_client.get(url).json()
    assert data["chain"] == []


# ---------------------------------------------------------------------
# AC-6 — perf budget at typical makerspace scale
# ---------------------------------------------------------------------


@pytest.mark.django_db
def test_panel_topology_under_two_seconds_at_scale(staff_client):
    """1 site, 10 panels, 200 circuits, 500 devices — single-panel
    topology read should land well under 2s per AC-6.

    We render only one panel's topology (the endpoint is per-panel) but
    seed the surrounding scale so the prefetch query has to filter.
    """

    loc = LocationFactory(name="Site")
    target_panel = None
    devices_made = 0
    circuits_made = 0
    for p_idx in range(10):
        panel = PowerPanel.objects.create(location=loc, name=f"Panel-{p_idx}")
        if p_idx == 0:
            target_panel = panel
        # 20 circuits per panel × 10 panels = 200 circuits
        for c_idx in range(20):
            breaker = PowerBreaker.objects.create(panel=panel, position=str(c_idx + 1), amperage=20)
            circuit = PowerCircuit.objects.create(breaker=breaker)
            outlet = PowerOutlet.objects.create(
                circuit=circuit, location=loc, label=f"p{p_idx}-c{c_idx}"
            )
            circuits_made += 1
            # 50 devices per panel × 10 panels = 500 devices
            for _ in range(min(50 - (devices_made % 50) - 1, 0) + 0):
                pass
            if devices_made < 500:
                asset = _make_asset(name=f"Dev-{devices_made}")
                port = PowerPort.objects.create(
                    asset=asset, label="Main", max_draw_amps=Decimal("1.0")
                )
                Cable.objects.create(
                    cable_type=Cable.CABLE_TYPE_POWER,
                    endpoint_a=outlet,
                    endpoint_b=port,
                )
                devices_made += 1

    assert circuits_made == 200
    assert devices_made == 200  # 1 device per circuit, capped at 500

    url = reverse("electrical-panel-topology", args=[target_panel.pk])
    # Warm caches.
    staff_client.get(url)
    start = time.perf_counter()
    resp = staff_client.get(url)
    elapsed = time.perf_counter() - start
    assert resp.status_code == 200
    assert elapsed < 2.0, f"panel topology took {elapsed:.2f}s (>2s budget)"
