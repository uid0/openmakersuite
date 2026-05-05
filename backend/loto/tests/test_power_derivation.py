"""
Tests for AssetEnergySource auto-derivation from the power topology (oms-qc3).

One test class per acceptance criterion in the bead.
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
from loto.services.power_derivation import (
    derive_for_asset,
    derive_for_cable,
    mark_stale_for_cable,
    rederive_all,
)

_tag_counter = itertools.count(1)


def _make_asset(**kwargs):
    kwargs.setdefault("asset_tag", f"DRV-{next(_tag_counter):06d}")
    return AssetFactory(**kwargs)


@pytest.fixture
def topology(db):
    """Sewing-room panel with a 240V/20A breaker, one outlet, one cabled asset."""

    loc = LocationFactory(name="Sewing Room")
    panel = PowerPanel.objects.create(location=loc, name="Sewing Panel", voltage=240)
    breaker = PowerBreaker.objects.create(panel=panel, position="14", amperage=20)
    circuit = PowerCircuit.objects.create(breaker=breaker)
    outlet = PowerOutlet.objects.create(circuit=circuit, location=loc, label="bench-1")
    asset = _make_asset(name="Sewing Machine", location=loc)
    port = PowerPort.objects.create(asset=asset, label="Main")
    cable = Cable.objects.create(
        cable_type=Cable.CABLE_TYPE_POWER,
        endpoint_a=outlet,
        endpoint_b=port,
    )
    padlock = LOTODevice.objects.create(
        device_type=LOTODevice.DEVICE_PADLOCK,
        label="SE-LK-007",
    )
    breaker.required_loto_devices.add(padlock)
    return {
        "loc": loc,
        "panel": panel,
        "breaker": breaker,
        "circuit": circuit,
        "outlet": outlet,
        "asset": asset,
        "port": port,
        "cable": cable,
        "padlock": padlock,
    }


# ----- AC-1 ---------------------------------------------------------------


class TestAC1_AutoPopulationFromTopology:
    """LOTO procedure shows derived energy source without manual entry."""

    def test_signal_creates_derived_source_on_cable_save(self, topology):
        """Saving the power cable triggers derivation via post_save signal."""

        asset = topology["asset"]
        sources = list(asset.energy_sources.filter(derived_from=topology["cable"]))
        assert len(sources) == 1, "expected exactly one derived row from the cable"
        derived = sources[0]
        assert derived.source_type == AssetEnergySource.SOURCE_ELECTRICAL
        assert derived.derived_from_id == topology["cable"].pk
        assert derived.is_stale is False

    def test_derived_source_describes_panel_and_breaker(self, topology):
        derived = topology["asset"].energy_sources.get(derived_from=topology["cable"])
        assert "240V" in derived.magnitude
        # AC-1: 'Sewing Panel breaker 14' style isolation hint
        assert "Sewing Panel" in derived.isolation_point
        assert "14" in derived.isolation_point

    def test_breaker_loto_devices_propagate_to_derived_source(self, topology):
        derived = topology["asset"].energy_sources.get(derived_from=topology["cable"])
        device_labels = list(derived.required_devices.values_list("label", flat=True))
        # AC-1: required LOTO 'padlock #SE-LK-007' propagates from the breaker
        assert "SE-LK-007" in device_labels

    def test_breaker_device_change_propagates_to_existing_source(self, topology):
        """Edit breaker LOTO requirements -> derived rows update."""

        new_lock = LOTODevice.objects.create(
            device_type=LOTODevice.DEVICE_BREAKER_LOCK,
            label="BL-99",
        )
        topology["breaker"].required_loto_devices.add(new_lock)

        derived = topology["asset"].energy_sources.get(derived_from=topology["cable"])
        labels = set(derived.required_devices.values_list("label", flat=True))
        assert {"SE-LK-007", "BL-99"}.issubset(labels)

    def test_idempotent_rederive_does_not_duplicate(self, topology):
        derive_for_asset(topology["asset"])
        derive_for_asset(topology["asset"])
        derive_for_cable(topology["cable"])

        count = topology["asset"].energy_sources.filter(derived_from=topology["cable"]).count()
        assert count == 1

    def test_asset_with_no_port_yields_no_derived_source(self, db):
        """Assets without a PowerPort are not auto-derived."""

        asset = _make_asset(name="Hand tool")
        result = derive_for_asset(asset)
        assert result is None
        assert asset.energy_sources.count() == 0


# ----- AC-2 ---------------------------------------------------------------


class TestAC2_ManualAndDerivedCoexist:
    """Manual entries (hydraulic, pneumatic) coexist with derived rows."""

    def test_manual_pneumatic_row_unaffected_by_cable_derivation(self, topology):
        manual = AssetEnergySource.objects.create(
            asset=topology["asset"],
            source_type=AssetEnergySource.SOURCE_PNEUMATIC,
            magnitude="80psi",
            isolation_point="ball valve under bench",
        )
        # Re-trigger derivation (simulates a cable update).
        derive_for_cable(topology["cable"])

        manual.refresh_from_db()
        assert manual.derived_from_id is None
        assert manual.is_stale is False
        assert manual.magnitude == "80psi"
        assert manual.isolation_point == "ball valve under bench"

        all_sources = topology["asset"].energy_sources.all()
        assert all_sources.count() == 2
        kinds = {s.source_type for s in all_sources}
        assert kinds == {
            AssetEnergySource.SOURCE_ELECTRICAL,
            AssetEnergySource.SOURCE_PNEUMATIC,
        }

    def test_manual_electrical_row_not_overwritten_by_derivation(self, topology):
        """A pre-existing manual electrical row (derived_from=NULL) survives."""

        manual = AssetEnergySource.objects.create(
            asset=topology["asset"],
            source_type=AssetEnergySource.SOURCE_ELECTRICAL,
            magnitude="manual notes",
            isolation_point="hand-recorded location",
        )
        derive_for_asset(topology["asset"])

        manual.refresh_from_db()
        assert manual.derived_from_id is None
        assert manual.magnitude == "manual notes"

        # The derived row exists alongside the manual one.
        derived = topology["asset"].energy_sources.filter(derived_from=topology["cable"])
        assert derived.count() == 1


# ----- AC-3 ---------------------------------------------------------------


class TestAC3_DecommissionedCableMarksStale:
    """Removed/decommissioned cables flag the derived row historical, not deleted."""

    def test_decommissioning_cable_marks_source_stale(self, topology):
        derived = topology["asset"].energy_sources.get(derived_from=topology["cable"])
        assert derived.is_stale is False

        topology["cable"].status = Cable.STATUS_DECOMMISSIONED
        topology["cable"].save()

        derived.refresh_from_db()
        assert derived.is_stale is True
        # Audit trail: the row still exists.
        assert AssetEnergySource.objects.filter(pk=derived.pk).exists()
        # FK preserved for forensics.
        assert derived.derived_from_id == topology["cable"].pk

    def test_deleting_cable_marks_source_stale(self, topology):
        derived = topology["asset"].energy_sources.get(derived_from=topology["cable"])
        cable_pk = topology["cable"].pk

        topology["cable"].delete()

        derived.refresh_from_db()
        assert derived.is_stale is True
        # SET_NULL cascade dropped the FK but the row survives.
        assert derived.derived_from_id is None
        assert AssetEnergySource.objects.filter(pk=derived.pk).exists()
        # And nothing else flagged stale by accident.
        assert AssetEnergySource.objects.filter(is_stale=True).count() == 1
        del cable_pk  # silence unused

    def test_mark_stale_for_cable_is_idempotent(self, topology):
        cable = topology["cable"]
        first = mark_stale_for_cable(cable)
        second = mark_stale_for_cable(cable)
        assert first == 1
        # Second call still finds the row (it exists with is_stale already True).
        assert second == 1

    def test_reconnecting_cable_clears_stale_flag(self, topology):
        derived = topology["asset"].energy_sources.get(derived_from=topology["cable"])
        topology["cable"].status = Cable.STATUS_DECOMMISSIONED
        topology["cable"].save()
        derived.refresh_from_db()
        assert derived.is_stale is True

        topology["cable"].status = Cable.STATUS_CONNECTED
        topology["cable"].save()
        derived.refresh_from_db()
        assert derived.is_stale is False


# ----- service-level coverage ---------------------------------------------


class TestRederiveAll:
    """The management-command entrypoint covers the full topology in one pass."""

    def test_rederive_all_fills_in_missing_rows(self, topology):
        # Drop the auto-derived row to simulate a stale install.
        AssetEnergySource.objects.filter(derived_from__isnull=False).delete()

        summary = rederive_all()

        assert summary["derived"] >= 1
        assert (
            AssetEnergySource.objects.filter(
                asset=topology["asset"],
                derived_from=topology["cable"],
            ).count()
            == 1
        )
