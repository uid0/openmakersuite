"""
Tests for the HardwiredConnection model (PR 1/5 of disconnect-promotion).

Covers: required disconnect FK, unique (asset, disconnect) constraint,
circuit-property pass-through, asset CASCADE, and disconnect PROTECT.
"""

from django.db import IntegrityError
from django.db.models import ProtectedError

import pytest

from electrical_circuits.models import (
    Disconnect,
    HardwiredConnection,
    PowerBreaker,
    PowerCircuit,
    PowerPanel,
)
from inventory.tests.factories import AssetFactory, LocationFactory


def _make_disconnect(disconnect_type=Disconnect.DISCONNECT_TYPE_UNFUSED, **kwargs):
    loc = LocationFactory()
    panel = PowerPanel.objects.create(location=loc, name=f"P-{loc.pk}")
    breaker = PowerBreaker.objects.create(panel=panel, position="1", amperage=30)
    circuit = PowerCircuit.objects.create(breaker=breaker)
    return Disconnect.objects.create(
        circuit=circuit,
        location=loc,
        label=kwargs.pop("label", "test disconnect"),
        disconnect_type=disconnect_type,
        **kwargs,
    )


@pytest.mark.django_db
def test_hardwired_connection_requires_disconnect():
    """The disconnect FK is required — every hardwired load resolves through one."""
    asset = AssetFactory()
    with pytest.raises(IntegrityError):
        HardwiredConnection.objects.create(asset=asset, disconnect=None)


@pytest.mark.django_db
def test_unique_asset_disconnect_pair():
    """Same (asset, disconnect) cannot be wired twice."""
    asset = AssetFactory()
    d = _make_disconnect()
    HardwiredConnection.objects.create(asset=asset, disconnect=d)
    with pytest.raises(IntegrityError):
        HardwiredConnection.objects.create(asset=asset, disconnect=d)


@pytest.mark.django_db
def test_same_asset_two_disconnects_allowed():
    """An asset with redundant feeds (rare but real) records two rows."""
    asset = AssetFactory()
    d1 = _make_disconnect(label="primary")
    d2 = _make_disconnect(label="standby")
    HardwiredConnection.objects.create(asset=asset, disconnect=d1)
    HardwiredConnection.objects.create(asset=asset, disconnect=d2)
    assert asset.hardwired_connections.count() == 2


@pytest.mark.django_db
def test_circuit_property_passes_through_disconnect():
    """``connection.circuit`` resolves through the disconnect — no duplicate FK."""
    asset = AssetFactory()
    d = _make_disconnect()
    hc = HardwiredConnection.objects.create(asset=asset, disconnect=d)
    assert hc.circuit == d.circuit
    assert hc.circuit.breaker == d.circuit.breaker


@pytest.mark.django_db
def test_asset_delete_cascades_connection():
    asset = AssetFactory()
    d = _make_disconnect()
    HardwiredConnection.objects.create(asset=asset, disconnect=d)
    asset.delete()
    assert HardwiredConnection.objects.count() == 0
    # Disconnect survives — only the link is removed.
    assert Disconnect.objects.filter(pk=d.pk).exists()


@pytest.mark.django_db
def test_disconnect_delete_protected_by_connection():
    """A disconnect with hardwired loads cannot be deleted out from under them."""
    asset = AssetFactory()
    d = _make_disconnect()
    HardwiredConnection.objects.create(asset=asset, disconnect=d)
    with pytest.raises(ProtectedError):
        d.delete()


@pytest.mark.django_db
def test_str_includes_asset_and_disconnect_label():
    asset = AssetFactory(name="Dust collector")
    d = _make_disconnect(label="DC-Wall-Switch")
    hc = HardwiredConnection.objects.create(asset=asset, disconnect=d)
    s = str(hc)
    assert "Dust collector" in s
    assert "DC-Wall-Switch" in s


@pytest.mark.django_db
def test_disconnect_type_none_supports_hardwired_load():
    """Even when there's no physical switch, the contract requires a Disconnect
    row (type='none') so every hardwired load resolves uniformly."""
    asset = AssetFactory()
    d = _make_disconnect(
        disconnect_type=Disconnect.DISCONNECT_TYPE_NONE,
        is_lockable=False,
    )
    hc = HardwiredConnection.objects.create(asset=asset, disconnect=d)
    assert hc.disconnect.disconnect_type == Disconnect.DISCONNECT_TYPE_NONE
    assert hc.circuit == d.circuit
