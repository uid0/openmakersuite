"""
Tests for AssetEnergySource auto-derivation from the asset → breaker FK.

The previous cable-driven derivation was retired along with the Cable model.
Derivation now runs off ``Asset.breaker`` directly: the asset.post_save
signal re-derives whenever a breaker FK changes, and breaker LOTO requirement
edits propagate to every derived row on the breaker.
"""

from __future__ import annotations

import itertools

import pytest

from electrical_circuits.models import PowerBreaker, PowerCircuit, PowerOutlet, PowerPanel
from inventory.tests.factories import AssetFactory, LocationFactory
from loto.models import AssetEnergySource, LOTODevice
from loto.services.power_derivation import (
    derive_for_asset,
    derive_for_breaker,
    mark_stale_for_breaker,
    rederive_all,
)

_tag_counter = itertools.count(1)


def _make_asset(**kwargs):
    kwargs.setdefault("asset_tag", f"DRV-{next(_tag_counter):06d}")
    return AssetFactory(**kwargs)


@pytest.fixture
def topology(db):
    """Sewing-room panel with a 240V/20A breaker and an asset on it."""

    loc = LocationFactory(name="Sewing Room")
    panel = PowerPanel.objects.create(location=loc, name="Sewing Panel", voltage=240)
    breaker = PowerBreaker.objects.create(panel=panel, position="14", amperage=20)
    circuit = PowerCircuit.objects.create(breaker=breaker)
    outlet = PowerOutlet.objects.create(circuit=circuit, location=loc, label="bench-1")
    padlock = LOTODevice.objects.create(
        device_type=LOTODevice.DEVICE_PADLOCK,
        label="SE-LK-007",
    )
    breaker.required_loto_devices.add(padlock)
    asset = _make_asset(name="Sewing Machine", location=loc, breaker=breaker)
    return {
        "loc": loc,
        "panel": panel,
        "breaker": breaker,
        "circuit": circuit,
        "outlet": outlet,
        "asset": asset,
        "padlock": padlock,
    }


class TestAutoDerivation:
    """LOTO procedure shows derived energy source without manual entry."""

    def test_signal_creates_derived_source_on_asset_save(self, topology):
        asset = topology["asset"]
        sources = list(asset.energy_sources.filter(derived_from=topology["breaker"]))
        assert len(sources) == 1
        derived = sources[0]
        assert derived.source_type == AssetEnergySource.SOURCE_ELECTRICAL
        assert derived.derived_from_id == topology["breaker"].pk
        assert derived.is_stale is False

    def test_derived_source_describes_panel_and_breaker(self, topology):
        derived = topology["asset"].energy_sources.get(derived_from=topology["breaker"])
        assert "240V" in derived.magnitude
        assert "Sewing Panel" in derived.isolation_point
        assert "14" in derived.isolation_point

    def test_breaker_loto_devices_propagate_to_derived_source(self, topology):
        derived = topology["asset"].energy_sources.get(derived_from=topology["breaker"])
        device_labels = list(derived.required_devices.values_list("label", flat=True))
        assert "SE-LK-007" in device_labels

    def test_breaker_device_change_propagates_to_existing_source(self, topology):
        new_lock = LOTODevice.objects.create(
            device_type=LOTODevice.DEVICE_BREAKER_LOCK,
            label="BL-99",
        )
        topology["breaker"].required_loto_devices.add(new_lock)

        derived = topology["asset"].energy_sources.get(derived_from=topology["breaker"])
        labels = set(derived.required_devices.values_list("label", flat=True))
        assert {"SE-LK-007", "BL-99"}.issubset(labels)

    def test_idempotent_rederive_does_not_duplicate(self, topology):
        derive_for_asset(topology["asset"])
        derive_for_asset(topology["asset"])
        derive_for_breaker(topology["breaker"])

        count = topology["asset"].energy_sources.filter(derived_from=topology["breaker"]).count()
        assert count == 1

    def test_asset_with_no_breaker_yields_no_derived_source(self, db):
        asset = _make_asset(name="Hand tool")
        result = derive_for_asset(asset)
        assert result is None
        assert asset.energy_sources.count() == 0


class TestManualAndDerivedCoexist:
    """Manual entries (hydraulic, pneumatic) coexist with derived rows."""

    def test_manual_pneumatic_row_unaffected_by_derivation(self, topology):
        manual = AssetEnergySource.objects.create(
            asset=topology["asset"],
            source_type=AssetEnergySource.SOURCE_PNEUMATIC,
            magnitude="80psi",
            isolation_point="ball valve under bench",
        )
        derive_for_breaker(topology["breaker"])

        manual.refresh_from_db()
        assert manual.derived_from_id is None
        assert manual.is_stale is False
        assert manual.magnitude == "80psi"

        all_sources = topology["asset"].energy_sources.all()
        assert all_sources.count() == 2
        kinds = {s.source_type for s in all_sources}
        assert kinds == {
            AssetEnergySource.SOURCE_ELECTRICAL,
            AssetEnergySource.SOURCE_PNEUMATIC,
        }

    def test_manual_electrical_row_not_overwritten_by_derivation(self, topology):
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

        derived = topology["asset"].energy_sources.filter(derived_from=topology["breaker"])
        assert derived.count() == 1


class TestBreakerDeletionMarksStale:
    """Removed/cleared breakers flag the derived row historical, not deleted."""

    def test_clearing_asset_breaker_marks_source_stale(self, topology):
        derived = topology["asset"].energy_sources.get(derived_from=topology["breaker"])
        assert derived.is_stale is False

        topology["asset"].breaker = None
        topology["asset"].save()

        derived.refresh_from_db()
        assert derived.is_stale is True
        assert AssetEnergySource.objects.filter(pk=derived.pk).exists()

    def test_deleting_breaker_marks_source_stale(self, topology):
        derived = topology["asset"].energy_sources.get(derived_from=topology["breaker"])

        # Drop the dependent outlet first — PowerCircuit → PowerOutlet is
        # PROTECT, so deleting a breaker requires its downstream outlets to
        # be gone (or moved). Production flow does this through the topology
        # editor; here we mimic that with a direct delete.
        topology["outlet"].delete()
        topology["breaker"].delete()

        derived.refresh_from_db()
        assert derived.is_stale is True
        # SET_NULL cascade dropped the FK but the row survives.
        assert derived.derived_from_id is None
        assert AssetEnergySource.objects.filter(pk=derived.pk).exists()

    def test_mark_stale_for_breaker_is_idempotent(self, topology):
        breaker = topology["breaker"]
        first = mark_stale_for_breaker(breaker)
        second = mark_stale_for_breaker(breaker)
        assert first == 1
        assert second == 1


class TestRederiveAll:
    def test_rederive_all_fills_in_missing_rows(self, topology):
        AssetEnergySource.objects.filter(derived_from__isnull=False).delete()

        summary = rederive_all()

        assert summary["derived"] >= 1
        assert (
            AssetEnergySource.objects.filter(
                asset=topology["asset"],
                derived_from=topology["breaker"],
            ).count()
            == 1
        )
