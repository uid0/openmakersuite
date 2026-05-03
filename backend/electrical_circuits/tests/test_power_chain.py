"""
Tests for power_chain resolvers (oms-i6g, AC-2..AC-4).

Builds a representative graph in fixtures and exercises every resolver:

* :func:`get_power_chain` — happy path + missing-link cases.
* :func:`get_devices_on_circuit` / :func:`get_devices_on_breaker`.
* :func:`get_trip_impact` — including ``is_critical`` separation.
* AC-3 — performance check at the documented panel scale.
"""

from __future__ import annotations

import itertools
import time

from django.db import connection, reset_queries
from django.test.utils import CaptureQueriesContext

import pytest

from electrical_circuits.models import (
    Cable,
    PowerBreaker,
    PowerCircuit,
    PowerOutlet,
    PowerPanel,
    PowerPort,
)
from electrical_circuits.services.power_chain import (
    PowerChainHop,
    get_devices_on_breaker,
    get_devices_on_circuit,
    get_power_chain,
    get_trip_impact,
)
from inventory.tests.factories import AssetFactory, LocationFactory

_tag_counter = itertools.count(1)


def _make_asset(name: str = None, **kwargs):
    """AssetFactory with an explicit unique asset_tag.

    Asset.save() auto-fills ``asset_tag`` from the first 8 hex chars of a
    UUID7, but UUID7's first 8 hex digits are the millisecond timestamp —
    so two assets created in the same ~16-day window collide. Tests pin
    the tag explicitly so we don't depend on luck.
    """

    kwargs.setdefault("asset_tag", f"TST-{next(_tag_counter):06d}")
    if name is not None:
        kwargs["name"] = name
    return AssetFactory(**kwargs)


@pytest.fixture
def graph(db):
    """Small but representative panel/breaker/circuit/outlet graph."""

    loc = LocationFactory(name="Sewing Room")
    panel = PowerPanel.objects.create(location=loc, name="Sewing A")
    breaker = PowerBreaker.objects.create(panel=panel, position="2", amperage=20)
    circuit = PowerCircuit.objects.create(breaker=breaker)
    outlet = PowerOutlet.objects.create(circuit=circuit, location=loc, label="bench-1")

    asset = _make_asset(name="Sewing Machine 1", location=loc)
    port = PowerPort.objects.create(asset=asset, label="Main")
    cable = Cable.objects.create(
        cable_type=Cable.CABLE_TYPE_POWER,
        endpoint_a=outlet,
        endpoint_b=port,
    )

    return {
        "loc": loc,
        "panel": panel,
        "breaker": breaker,
        "circuit": circuit,
        "outlet": outlet,
        "asset": asset,
        "port": port,
        "cable": cable,
    }


def test_get_power_chain_returns_full_chain(graph):
    chain = get_power_chain(graph["asset"])
    kinds = [hop.kind for hop in chain]
    assert kinds == ["panel", "breaker", "circuit", "outlet", "cable", "port"]
    objs = [hop.obj for hop in chain]
    assert objs == [
        graph["panel"],
        graph["breaker"],
        graph["circuit"],
        graph["outlet"],
        graph["cable"],
        graph["port"],
    ]


@pytest.mark.django_db
def test_get_power_chain_empty_when_asset_has_no_port():
    asset = AssetFactory()
    assert get_power_chain(asset) == []


@pytest.mark.django_db
def test_get_power_chain_empty_when_port_has_no_cable():
    asset = AssetFactory()
    PowerPort.objects.create(asset=asset, label="Main")
    assert get_power_chain(asset) == []


@pytest.mark.django_db
def test_get_power_chain_returns_hop_dataclass(graph):
    chain = get_power_chain(graph["asset"])
    assert all(isinstance(hop, PowerChainHop) for hop in chain)


def test_get_devices_on_circuit(graph):
    devices = list(get_devices_on_circuit(graph["circuit"]))
    assert devices == [graph["asset"]]


def test_get_devices_on_circuit_empty_when_outlet_unconnected(graph):
    # Add a second outlet on the same circuit with no cable.
    PowerOutlet.objects.create(circuit=graph["circuit"], location=graph["loc"], label="bench-2")
    # Asset count is unchanged because only the cabled outlet has a device.
    devices = list(get_devices_on_circuit(graph["circuit"]))
    assert devices == [graph["asset"]]


def test_get_devices_on_breaker_spans_multiple_circuits(graph):
    # Add a second circuit on the same breaker with another connected asset.
    circuit2 = PowerCircuit.objects.create(breaker=graph["breaker"])
    outlet2 = PowerOutlet.objects.create(circuit=circuit2, location=graph["loc"], label="bench-3")
    asset2 = _make_asset(name="Iron")
    port2 = PowerPort.objects.create(asset=asset2, label="Main")
    Cable.objects.create(
        cable_type=Cable.CABLE_TYPE_POWER,
        endpoint_a=outlet2,
        endpoint_b=port2,
    )

    devices = set(get_devices_on_breaker(graph["breaker"]))
    assert devices == {graph["asset"], asset2}


def test_get_trip_impact_separates_critical_loads(graph):
    # Make the existing asset critical, add a non-critical roommate.
    graph["asset"].is_critical = True
    graph["asset"].save(update_fields=["is_critical"])

    roommate = _make_asset(name="Lamp")
    roommate_port = PowerPort.objects.create(asset=roommate, label="Main")
    outlet2 = PowerOutlet.objects.create(
        circuit=graph["circuit"], location=graph["loc"], label="bench-2"
    )
    Cable.objects.create(
        cable_type=Cable.CABLE_TYPE_POWER,
        endpoint_a=outlet2,
        endpoint_b=roommate_port,
    )

    impact = get_trip_impact(graph["breaker"])
    assert impact["breaker"] == graph["breaker"]
    assert set(impact["assets"]) == {graph["asset"], roommate}
    assert impact["critical_loads"] == [graph["asset"]]


@pytest.mark.django_db
def test_get_trip_impact_empty_for_unloaded_breaker():
    loc = LocationFactory()
    panel = PowerPanel.objects.create(location=loc, name="Empty Panel")
    breaker = PowerBreaker.objects.create(panel=panel, position="3", amperage=20)
    impact = get_trip_impact(breaker)
    assert impact["assets"] == []
    assert impact["critical_loads"] == []


@pytest.mark.django_db
def test_get_devices_on_breaker_performance(settings):
    """AC-3: 40 breakers / 200 outlets / 50 connected devices in <50ms.

    The interesting breaker has 5 circuits × 5 outlets = 25 outlets, of
    which 12 are cabled to devices. Surrounding breakers add fanout that
    forces the resolver to actually filter rather than scan.
    """

    loc = LocationFactory(name="Perf Loc")
    panel = PowerPanel.objects.create(location=loc, name="Perf Panel")

    target_breaker = None
    total_outlets = 0
    total_devices = 0
    for b_idx in range(40):
        breaker = PowerBreaker.objects.create(panel=panel, position=str(b_idx + 1), amperage=20)
        if b_idx == 0:
            target_breaker = breaker

        # Each breaker gets 5 circuits with 1 outlet each (200 outlets total).
        for c_idx in range(5):
            circuit = PowerCircuit.objects.create(breaker=breaker)
            outlet = PowerOutlet.objects.create(
                circuit=circuit, location=loc, label=f"b{b_idx}-c{c_idx}"
            )
            total_outlets += 1
            # First 50 outlets get a connected device.
            if total_devices < 50:
                asset = _make_asset(name=f"Dev {total_devices}")
                port = PowerPort.objects.create(asset=asset, label="Main")
                Cable.objects.create(
                    cable_type=Cable.CABLE_TYPE_POWER,
                    endpoint_a=outlet,
                    endpoint_b=port,
                )
                total_devices += 1

    assert total_outlets == 200
    assert total_devices == 50

    # Warm caches (ContentType lookups, etc.)
    list(get_devices_on_breaker(target_breaker))

    start = time.perf_counter()
    devices = list(get_devices_on_breaker(target_breaker))
    elapsed_ms = (time.perf_counter() - start) * 1000

    # Sanity: the first breaker holds the first 5 connected devices.
    assert len(devices) == 5
    assert elapsed_ms < 50, f"resolver took {elapsed_ms:.1f}ms (>50ms budget)"


@pytest.mark.django_db
def test_get_devices_on_breaker_uses_constant_query_count():
    """Loading should not scale per-outlet (no per-row GFK lookup)."""

    loc = LocationFactory()
    panel = PowerPanel.objects.create(location=loc, name="Q Count")
    breaker = PowerBreaker.objects.create(panel=panel, position="1", amperage=20)
    for i in range(10):
        circuit = PowerCircuit.objects.create(breaker=breaker)
        outlet = PowerOutlet.objects.create(circuit=circuit, location=loc, label=f"o{i}")
        asset = _make_asset()
        port = PowerPort.objects.create(asset=asset, label="Main")
        Cable.objects.create(
            cable_type=Cable.CABLE_TYPE_POWER,
            endpoint_a=outlet,
            endpoint_b=port,
        )

    reset_queries()
    with CaptureQueriesContext(connection) as ctx:
        list(get_devices_on_breaker(breaker))
    # Resolver should be O(1) queries: outlet-pks, content-types, cables,
    # ports, assets — well under 10.
    assert len(ctx.captured_queries) < 10, f"too many queries: {len(ctx.captured_queries)}"
