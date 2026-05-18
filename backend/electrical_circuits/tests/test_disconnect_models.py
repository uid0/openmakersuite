"""
Tests for the Disconnect model (PR 1/5 of disconnect-promotion).

Covers each disconnect_type round-trip, the two clean()-level warnings that
flag ``needs_review`` instead of rejecting saves, the optional location, and
the PowerOutlet.disconnect SET_NULL relationship.
"""

from django.db.models import ProtectedError

import pytest

from electrical_circuits.models import (
    Disconnect,
    PowerBreaker,
    PowerCircuit,
    PowerOutlet,
    PowerPanel,
)
from inventory.tests.factories import LocationFactory
from loto.models import LOTODevice


def _make_circuit():
    loc = LocationFactory()
    panel = PowerPanel.objects.create(location=loc, name=f"P-{loc.pk}")
    breaker = PowerBreaker.objects.create(panel=panel, position="1", amperage=30)
    return PowerCircuit.objects.create(breaker=breaker), loc


@pytest.mark.django_db
@pytest.mark.parametrize(
    "disconnect_type",
    [
        Disconnect.DISCONNECT_TYPE_FUSED,
        Disconnect.DISCONNECT_TYPE_UNFUSED,
        Disconnect.DISCONNECT_TYPE_TOGGLE,
        Disconnect.DISCONNECT_TYPE_INTEGRAL,
        Disconnect.DISCONNECT_TYPE_NONE,
    ],
)
def test_disconnect_creation_round_trips_each_type(disconnect_type):
    """Every choice value must persist and survive a refresh."""
    circuit, loc = _make_circuit()
    # 'integral' and 'none' types are commonly recorded without is_lockable;
    # set False to avoid tripping the clean() warning we cover separately.
    is_lockable = disconnect_type not in (
        Disconnect.DISCONNECT_TYPE_INTEGRAL,
        Disconnect.DISCONNECT_TYPE_NONE,
    )
    d = Disconnect.objects.create(
        circuit=circuit,
        location=loc,
        label=f"d-{disconnect_type}",
        disconnect_type=disconnect_type,
        is_lockable=is_lockable,
        fuse_size="30A" if disconnect_type == Disconnect.DISCONNECT_TYPE_FUSED else "",
    )
    d.refresh_from_db()
    assert d.disconnect_type == disconnect_type
    assert d.needs_review is False


@pytest.mark.django_db
def test_disconnect_str_includes_label_and_type_display():
    circuit, loc = _make_circuit()
    d = Disconnect.objects.create(
        circuit=circuit,
        location=loc,
        label="Welder disconnect",
        disconnect_type=Disconnect.DISCONNECT_TYPE_UNFUSED,
    )
    assert "Welder disconnect" in str(d)
    assert "Unfused safety switch" in str(d)


@pytest.mark.django_db
def test_location_is_optional():
    """Integral / none types may not have a separate mount location."""
    circuit, _ = _make_circuit()
    d = Disconnect.objects.create(
        circuit=circuit,
        location=None,
        label="integral",
        disconnect_type=Disconnect.DISCONNECT_TYPE_INTEGRAL,
        is_lockable=False,
    )
    assert d.location is None


@pytest.mark.django_db
def test_clean_flags_fused_without_fuse_size():
    """Fused disconnect missing fuse_size → needs_review without rejection."""
    circuit, loc = _make_circuit()
    d = Disconnect(
        circuit=circuit,
        location=loc,
        label="missing fuse spec",
        disconnect_type=Disconnect.DISCONNECT_TYPE_FUSED,
        fuse_size="",
    )
    d.full_clean()  # must NOT raise
    assert d.needs_review is True
    d.save()
    assert Disconnect.objects.get(pk=d.pk).needs_review is True


@pytest.mark.django_db
def test_clean_does_not_flag_fused_with_fuse_size():
    circuit, loc = _make_circuit()
    d = Disconnect(
        circuit=circuit,
        location=loc,
        label="ok fused",
        disconnect_type=Disconnect.DISCONNECT_TYPE_FUSED,
        fuse_size="30A class J",
    )
    d.full_clean()
    assert d.needs_review is False


@pytest.mark.django_db
@pytest.mark.parametrize(
    "disconnect_type",
    [Disconnect.DISCONNECT_TYPE_INTEGRAL, Disconnect.DISCONNECT_TYPE_NONE],
)
def test_clean_flags_lockable_on_integral_or_none(disconnect_type):
    """is_lockable=True on integral/none → needs_review without rejection."""
    circuit, loc = _make_circuit()
    d = Disconnect(
        circuit=circuit,
        location=loc,
        label=f"lockable {disconnect_type}",
        disconnect_type=disconnect_type,
        is_lockable=True,
    )
    d.full_clean()  # must NOT raise
    assert d.needs_review is True


@pytest.mark.django_db
def test_clean_does_not_flag_integral_when_not_lockable():
    circuit, _ = _make_circuit()
    d = Disconnect(
        circuit=circuit,
        location=None,
        label="integral, not lockable",
        disconnect_type=Disconnect.DISCONNECT_TYPE_INTEGRAL,
        is_lockable=False,
    )
    d.full_clean()
    assert d.needs_review is False


@pytest.mark.django_db
def test_circuit_protects_disconnect_on_delete():
    """Deleting the parent circuit must be blocked while a disconnect exists."""
    circuit, loc = _make_circuit()
    Disconnect.objects.create(
        circuit=circuit,
        location=loc,
        label="blocker",
        disconnect_type=Disconnect.DISCONNECT_TYPE_UNFUSED,
    )
    with pytest.raises(ProtectedError):
        circuit.delete()


@pytest.mark.django_db
def test_required_loto_devices_m2m_round_trip():
    circuit, loc = _make_circuit()
    pad = LOTODevice.objects.create(device_type=LOTODevice.DEVICE_PADLOCK, label="PAD-1")
    tag = LOTODevice.objects.create(device_type=LOTODevice.DEVICE_TAG, label="TAG-1")
    d = Disconnect.objects.create(
        circuit=circuit,
        location=loc,
        label="loto-attach",
        disconnect_type=Disconnect.DISCONNECT_TYPE_FUSED,
        fuse_size="30A",
    )
    d.required_loto_devices.add(pad, tag)
    assert set(d.required_loto_devices.all()) == {pad, tag}
    assert pad.disconnects.first() == d


@pytest.mark.django_db
def test_power_outlet_disconnect_set_null_on_delete():
    """SET_NULL on PowerOutlet.disconnect leaves outlet intact when the
    disconnect is removed."""
    circuit, loc = _make_circuit()
    d = Disconnect.objects.create(
        circuit=circuit,
        location=loc,
        label="upstream",
        disconnect_type=Disconnect.DISCONNECT_TYPE_UNFUSED,
    )
    outlet = PowerOutlet.objects.create(
        circuit=circuit,
        location=loc,
        disconnect=d,
        label="welder",
        outlet_type="L6-30R",
    )
    assert outlet.disconnect == d
    assert d.powered_outlets.first() == outlet
    d.delete()
    outlet.refresh_from_db()
    assert outlet.disconnect is None
    # Outlet still exists; only the link is severed.
    assert PowerOutlet.objects.filter(pk=outlet.pk).exists()


@pytest.mark.django_db
def test_power_outlet_disconnect_optional():
    """The new disconnect FK on PowerOutlet is nullable for backward compat."""
    circuit, loc = _make_circuit()
    outlet = PowerOutlet.objects.create(
        circuit=circuit,
        location=loc,
        label="ordinary",
        outlet_type="5-15R",
    )
    assert outlet.disconnect is None
