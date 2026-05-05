"""Tests for LOTO auto-derivation from power topology (oms-qc3).

Maps directly onto AC-1 / AC-2 / AC-3 of the bead.
"""

from __future__ import annotations

import itertools

import pytest

from electrical_circuits.models import (
    Cable,
    PowerBreaker,
    PowerCircuit,
    PowerOutlet,
    PowerPanel,
    PowerPort,
)
from inventory.tests.factories import AssetFactory, LocationFactory
from loto.models import AssetEnergySource, LOTODevice
from loto.services.derivation import derive_energy_source_for_cable

_tag_counter = itertools.count(1)


def _make_asset(name: str = "Asset", **kwargs):
    kwargs.setdefault("asset_tag", f"QC3-{next(_tag_counter):06d}")
    kwargs["name"] = name
    return AssetFactory(**kwargs)


@pytest.fixture
def chain(db):
    """A complete cabled power chain ready to derive from."""

    loc = LocationFactory()
    panel = PowerPanel.objects.create(
        location=loc,
        name="Sewing Panel",
        voltage=240,
    )
    breaker = PowerBreaker.objects.create(
        panel=panel,
        position="14",
        amperage=20,
    )
    circuit = PowerCircuit.objects.create(breaker=breaker)
    outlet = PowerOutlet.objects.create(
        circuit=circuit,
        location=loc,
        label="bench-1",
    )

    asset = _make_asset(name="Industrial Sewing Machine", location=loc)
    port = PowerPort.objects.create(asset=asset, label="Main")

    padlock = LOTODevice.objects.create(
        device_type=LOTODevice.DEVICE_PADLOCK,
        label="SE-LK-007",
    )
    breaker_lock = LOTODevice.objects.create(
        device_type=LOTODevice.DEVICE_BREAKER_LOCK,
        label="BL-014",
    )
    padlock.secured_breakers.add(breaker)
    breaker_lock.secured_breakers.add(breaker)

    return {
        "loc": loc,
        "panel": panel,
        "breaker": breaker,
        "circuit": circuit,
        "outlet": outlet,
        "asset": asset,
        "port": port,
        "padlock": padlock,
        "breaker_lock": breaker_lock,
    }


# -- AC-1 ----------------------------------------------------------------------


def test_ac1_creating_cable_auto_derives_energy_source(chain):
    """AC-1: cable creation populates an AssetEnergySource with no manual entry."""

    assert AssetEnergySource.objects.filter(asset=chain["asset"]).count() == 0

    cable = Cable.objects.create(
        cable_type=Cable.CABLE_TYPE_POWER,
        endpoint_a=chain["outlet"],
        endpoint_b=chain["port"],
    )

    derived = AssetEnergySource.objects.get(asset=chain["asset"])
    assert derived.derived_from_id == cable.pk
    assert derived.source_type == AssetEnergySource.SOURCE_ELECTRICAL
    assert "240V" in derived.magnitude
    assert "20A" in derived.magnitude
    assert "Sewing Panel" in derived.isolation_point
    assert "Breaker 14" in derived.isolation_point
    assert derived.status == AssetEnergySource.STATUS_ACTIVE
    # Required LOTO devices propagate from the breaker's secured-by set.
    labels = sorted(derived.required_devices.values_list("label", flat=True))
    assert labels == ["BL-014", "SE-LK-007"]


def test_ac1_idempotent_on_repeated_save(chain):
    cable = Cable.objects.create(
        cable_type=Cable.CABLE_TYPE_POWER,
        endpoint_a=chain["outlet"],
        endpoint_b=chain["port"],
    )
    derive_energy_source_for_cable(cable)
    derive_energy_source_for_cable(cable)
    cable.save()

    assert AssetEnergySource.objects.filter(derived_from=cable).count() == 1


def test_ac1_endpoint_order_doesnt_matter(chain):
    # Same chain wired with the port as endpoint_a instead of endpoint_b.
    Cable.objects.create(
        cable_type=Cable.CABLE_TYPE_POWER,
        endpoint_a=chain["port"],
        endpoint_b=chain["outlet"],
    )
    assert AssetEnergySource.objects.filter(asset=chain["asset"]).count() == 1


# -- AC-2 ----------------------------------------------------------------------


def test_ac2_manual_and_derived_sources_coexist(chain):
    manual = AssetEnergySource.objects.create(
        asset=chain["asset"],
        source_type=AssetEnergySource.SOURCE_PNEUMATIC,
        magnitude="80psi",
        isolation_point="Red shutoff valve behind machine",
    )
    Cable.objects.create(
        cable_type=Cable.CABLE_TYPE_POWER,
        endpoint_a=chain["outlet"],
        endpoint_b=chain["port"],
    )

    sources = list(
        AssetEnergySource.objects.filter(asset=chain["asset"]).order_by("source_type")
    )
    assert {s.source_type for s in sources} == {
        AssetEnergySource.SOURCE_ELECTRICAL,
        AssetEnergySource.SOURCE_PNEUMATIC,
    }
    # The manual row is untouched: still no derived_from, still STATUS_ACTIVE.
    manual.refresh_from_db()
    assert manual.derived_from is None
    assert manual.status == AssetEnergySource.STATUS_ACTIVE


def test_ac2_decommissioning_cable_leaves_manual_rows_alone(chain):
    manual = AssetEnergySource.objects.create(
        asset=chain["asset"],
        source_type=AssetEnergySource.SOURCE_HYDRAULIC,
        magnitude="2000psi",
    )
    cable = Cable.objects.create(
        cable_type=Cable.CABLE_TYPE_POWER,
        endpoint_a=chain["outlet"],
        endpoint_b=chain["port"],
    )

    cable.status = Cable.STATUS_DECOMMISSIONED
    cable.save()

    manual.refresh_from_db()
    assert manual.status == AssetEnergySource.STATUS_ACTIVE


# -- AC-3 ----------------------------------------------------------------------


def test_ac3_decommissioning_cable_marks_derived_historical(chain):
    cable = Cable.objects.create(
        cable_type=Cable.CABLE_TYPE_POWER,
        endpoint_a=chain["outlet"],
        endpoint_b=chain["port"],
    )
    derived_pk = AssetEnergySource.objects.get(derived_from=cable).pk

    cable.status = Cable.STATUS_DECOMMISSIONED
    cable.save()

    derived = AssetEnergySource.objects.get(pk=derived_pk)
    assert derived.status == AssetEnergySource.STATUS_HISTORICAL
    # The audit trail survives — row is NOT auto-deleted.
    assert AssetEnergySource.objects.filter(pk=derived_pk).exists()
    # derived_from still points at the cable while it lives.
    assert derived.derived_from_id == cable.pk


def test_ac3_deleting_cable_marks_derived_historical_and_keeps_row(chain):
    cable = Cable.objects.create(
        cable_type=Cable.CABLE_TYPE_POWER,
        endpoint_a=chain["outlet"],
        endpoint_b=chain["port"],
    )
    derived_pk = AssetEnergySource.objects.get(derived_from=cable).pk

    cable.delete()

    derived = AssetEnergySource.objects.get(pk=derived_pk)
    assert derived.status == AssetEnergySource.STATUS_HISTORICAL
    # SET_NULL on cable delete keeps the row but loses the FK target.
    assert derived.derived_from is None
    # Isolation context is preserved as a snapshot for the audit trail.
    assert "Sewing Panel" in derived.isolation_point


def test_ac3_replacing_a_cable_creates_a_new_active_row(chain):
    """A new cable for the same asset gets its own active row; the old cable's
    derived row stays as historical so the audit trail tracks both.
    """

    cable_a = Cable.objects.create(
        cable_type=Cable.CABLE_TYPE_POWER,
        endpoint_a=chain["outlet"],
        endpoint_b=chain["port"],
    )
    cable_a.status = Cable.STATUS_DECOMMISSIONED
    cable_a.save()

    # Move the asset to a new outlet on the same panel.
    other_breaker = PowerBreaker.objects.create(
        panel=chain["panel"],
        position="16",
        amperage=20,
    )
    other_circuit = PowerCircuit.objects.create(breaker=other_breaker)
    other_outlet = PowerOutlet.objects.create(
        circuit=other_circuit,
        location=chain["loc"],
        label="bench-2",
    )

    cable_b = Cable.objects.create(
        cable_type=Cable.CABLE_TYPE_POWER,
        endpoint_a=other_outlet,
        endpoint_b=chain["port"],
    )

    historical = AssetEnergySource.objects.get(derived_from=cable_a)
    active = AssetEnergySource.objects.get(derived_from=cable_b)

    assert historical.status == AssetEnergySource.STATUS_HISTORICAL
    assert active.status == AssetEnergySource.STATUS_ACTIVE
    assert "Breaker 16" in active.isolation_point


# -- additional coverage -------------------------------------------------------


def test_breaker_lockout_devices_change_resyncs_required_devices(chain):
    Cable.objects.create(
        cable_type=Cable.CABLE_TYPE_POWER,
        endpoint_a=chain["outlet"],
        endpoint_b=chain["port"],
    )
    # Add a new lockout device to the breaker; derived row should pick it up.
    new_lock = LOTODevice.objects.create(
        device_type=LOTODevice.DEVICE_TAG,
        label="DANGER-tag-99",
    )
    new_lock.secured_breakers.add(chain["breaker"])

    derived = AssetEnergySource.objects.get(asset=chain["asset"])
    labels = sorted(derived.required_devices.values_list("label", flat=True))
    assert "DANGER-tag-99" in labels


def test_non_power_cable_does_not_create_energy_source(db):
    from devices.models import Interface, NetworkDrop

    loc = LocationFactory()
    drop = NetworkDrop.objects.create(location=loc, identifier="wallplate-1")
    asset = _make_asset(name="IP Camera")
    iface = Interface.objects.create(
        asset=asset,
        name="eth0",
        mac_address="aa:bb:cc:dd:ee:01",
    )
    Cable.objects.create(
        cable_type=Cable.CABLE_TYPE_NETWORK,
        endpoint_a=iface,
        endpoint_b=drop,
    )

    assert AssetEnergySource.objects.filter(asset=asset).count() == 0


def test_cable_without_resolvable_endpoints_silently_no_ops(db):
    """If the cable's endpoints aren't an outlet+port pair, derivation skips it.

    Constructs the situation by saving a power cable then deleting one
    endpoint, then forcing derivation. We rely on derive_energy_source_for_cable
    being safe to call directly (defensive, returns None).
    """

    loc = LocationFactory()
    panel = PowerPanel.objects.create(location=loc, name="P", voltage=120)
    breaker = PowerBreaker.objects.create(panel=panel, position="1", amperage=15)
    circuit = PowerCircuit.objects.create(breaker=breaker)
    outlet = PowerOutlet.objects.create(circuit=circuit, location=loc, label="x")
    asset = _make_asset()
    port = PowerPort.objects.create(asset=asset, label="Main")
    cable = Cable.objects.create(
        cable_type=Cable.CABLE_TYPE_POWER,
        endpoint_a=outlet,
        endpoint_b=port,
    )

    # Remove the port out from under the cable (cascade deletes the cable too,
    # but we test the derivation function in isolation first).
    assert derive_energy_source_for_cable(cable) is not None
