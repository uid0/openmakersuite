"""
Tests for the Cable model (oms-i6g, AC-1).

Verifies the generic-FK pairing rules: power cables pair PowerOutlet
with PowerPort, network cables pair devices.Interface with
devices.NetworkDrop. Anything else is rejected by ``full_clean``.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError

import pytest

from devices.models import Interface as DeviceInterface
from devices.models import NetworkDrop as DeviceNetworkDrop
from electrical_circuits.models import (
    Cable,
    PowerBreaker,
    PowerCircuit,
    PowerOutlet,
    PowerPanel,
    PowerPort,
)
from inventory.tests.factories import AssetFactory, LocationFactory


def _make_power_outlet():
    loc = LocationFactory()
    panel = PowerPanel.objects.create(location=loc, name=f"Panel {loc.pk}")
    breaker = PowerBreaker.objects.create(panel=panel, position="2", amperage=20)
    circuit = PowerCircuit.objects.create(breaker=breaker)
    outlet = PowerOutlet.objects.create(circuit=circuit, location=loc, label="o1")
    return outlet


def _make_power_port():
    asset = AssetFactory()
    return PowerPort.objects.create(asset=asset, label="Main")


def _make_interface():
    asset = AssetFactory()
    return DeviceInterface.objects.create(asset=asset, name="eth0", mac_address="aabbccddeeff")


def _make_network_drop():
    loc = LocationFactory()
    return DeviceNetworkDrop.objects.create(location=loc, drop_label="A12")


@pytest.mark.django_db
def test_power_cable_outlet_to_port_validates():
    outlet = _make_power_outlet()
    port = _make_power_port()
    cable = Cable.objects.create(
        cable_type=Cable.CABLE_TYPE_POWER,
        endpoint_a=outlet,
        endpoint_b=port,
    )
    cable.refresh_from_db()
    assert cable.endpoint_a == outlet
    assert cable.endpoint_b == port


@pytest.mark.django_db
def test_power_cable_endpoints_can_be_swapped():
    """Order does not matter — port-first should work too."""
    outlet = _make_power_outlet()
    port = _make_power_port()
    cable = Cable.objects.create(
        cable_type=Cable.CABLE_TYPE_POWER,
        endpoint_a=port,
        endpoint_b=outlet,
    )
    assert cable.pk is not None


@pytest.mark.django_db
def test_power_cable_rejects_non_power_pair():
    outlet = _make_power_outlet()
    drop = _make_network_drop()
    with pytest.raises(ValidationError):
        Cable.objects.create(
            cable_type=Cable.CABLE_TYPE_POWER,
            endpoint_a=outlet,
            endpoint_b=drop,
        )


@pytest.mark.django_db
def test_power_cable_rejects_two_outlets():
    a = _make_power_outlet()
    b = _make_power_outlet()
    with pytest.raises(ValidationError):
        Cable.objects.create(
            cable_type=Cable.CABLE_TYPE_POWER,
            endpoint_a=a,
            endpoint_b=b,
        )


@pytest.mark.django_db
def test_network_cable_interface_to_drop_validates():
    iface = _make_interface()
    drop = _make_network_drop()
    cable = Cable.objects.create(
        cable_type=Cable.CABLE_TYPE_NETWORK,
        endpoint_a=iface,
        endpoint_b=drop,
    )
    assert cable.pk is not None
    assert cable.endpoint_a == iface
    assert cable.endpoint_b == drop


@pytest.mark.django_db
def test_network_cable_rejects_power_endpoints():
    outlet = _make_power_outlet()
    port = _make_power_port()
    with pytest.raises(ValidationError):
        Cable.objects.create(
            cable_type=Cable.CABLE_TYPE_NETWORK,
            endpoint_a=outlet,
            endpoint_b=port,
        )


@pytest.mark.django_db
def test_cable_rejects_self_loop():
    port = _make_power_port()
    with pytest.raises(ValidationError):
        Cable.objects.create(
            cable_type=Cable.CABLE_TYPE_POWER,
            endpoint_a=port,
            endpoint_b=port,
        )
