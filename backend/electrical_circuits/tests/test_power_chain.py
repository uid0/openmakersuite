"""
Tests for power_chain resolvers.

The resolvers used to walk a generic-FK cable graph; after the cable removal
they walk the direct ``Asset.breaker`` association. The shape of the public
API hasn't changed but the chain hops are shorter (no more ``cable``/``port``
hops).
"""

from __future__ import annotations

import itertools

import pytest

from electrical_circuits.models import PowerBreaker, PowerCircuit, PowerOutlet, PowerPanel
from electrical_circuits.services.power_chain import (
    PowerChainHop,
    get_devices_on_breaker,
    get_devices_on_circuit,
    get_power_chain,
    get_trip_impact,
)
from inventory.tests.factories import AssetFactory, LocationFactory

_tag_counter = itertools.count(1)


def _make_asset(name: str | None = None, **kwargs):
    """AssetFactory with an explicit unique asset_tag."""

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

    asset = _make_asset(name="Sewing Machine 1", location=loc, breaker=breaker)

    return {
        "loc": loc,
        "panel": panel,
        "breaker": breaker,
        "circuit": circuit,
        "outlet": outlet,
        "asset": asset,
    }


def test_get_power_chain_returns_full_chain(graph):
    chain = get_power_chain(graph["asset"])
    kinds = [hop.kind for hop in chain]
    assert kinds == ["panel", "breaker", "circuit"]
    objs = [hop.obj for hop in chain]
    assert objs == [graph["panel"], graph["breaker"], graph["circuit"]]


@pytest.mark.django_db
def test_get_power_chain_empty_when_asset_has_no_breaker():
    asset = AssetFactory()
    assert get_power_chain(asset) == []


def test_get_power_chain_drops_circuit_hop_when_ambiguous(graph):
    # Multi-wire branch: two circuits on one breaker — the resolver can't
    # tell which circuit the asset is on, so the circuit hop is omitted.
    PowerCircuit.objects.create(breaker=graph["breaker"])
    chain = get_power_chain(graph["asset"])
    assert [hop.kind for hop in chain] == ["panel", "breaker"]


@pytest.mark.django_db
def test_get_power_chain_returns_hop_dataclass(graph):
    chain = get_power_chain(graph["asset"])
    assert all(isinstance(hop, PowerChainHop) for hop in chain)


def test_get_devices_on_circuit(graph):
    devices = list(get_devices_on_circuit(graph["circuit"]))
    assert devices == [graph["asset"]]


def test_get_devices_on_breaker_spans_multiple_circuits(graph):
    asset2 = _make_asset(name="Iron", breaker=graph["breaker"])
    devices = set(get_devices_on_breaker(graph["breaker"]))
    assert devices == {graph["asset"], asset2}


def test_get_trip_impact_separates_critical_loads(graph):
    graph["asset"].is_critical = True
    graph["asset"].save(update_fields=["is_critical"])
    roommate = _make_asset(name="Lamp", breaker=graph["breaker"])

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
