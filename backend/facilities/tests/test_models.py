"""Model + Asset compatibility-layer tests for facilities.AssetSiteRequirements.

Covers the #880 refactor contract:
* the 1:1 profile model itself,
* the Asset read/write compat properties (default reads without a profile,
  write-through setters that create/update the profile),
* the ``circuit`` read-only shim, and
* the ``PowerBreaker.assets`` / ``Disconnect.assets`` reverse-relation shims.
"""

import pytest

from electrical_circuits.models import Disconnect, PowerBreaker, PowerCircuit, PowerPanel
from facilities.models import AssetSiteRequirements
from inventory.tests.factories import AssetFactory

pytestmark = pytest.mark.django_db


def _make_breaker(location, position="1"):
    panel = PowerPanel.objects.create(location=location, name=f"Panel {position}")
    return PowerBreaker.objects.create(panel=panel, position=position, amperage=20)


def _make_disconnect(breaker):
    circuit = PowerCircuit.objects.create(breaker=breaker)
    return Disconnect.objects.create(
        circuit=circuit, label="D1", disconnect_type=Disconnect.DISCONNECT_TYPE_UNFUSED
    )


class TestAssetSiteRequirementsModel:
    def test_str(self):
        asset = AssetFactory(name="Lathe")
        profile = AssetSiteRequirements.objects.create(asset=asset)
        assert str(profile) == f"Site requirements for {asset}"

    def test_one_to_one_reverse_accessor(self):
        asset = AssetFactory()
        profile = AssetSiteRequirements.objects.create(asset=asset, needs_chilling=True)
        assert asset.site_requirements == profile


class TestAssetCompatReads:
    def test_defaults_without_profile(self):
        asset = AssetFactory()
        # No profile row exists → every accessor returns its default.
        assert asset.breaker is None
        assert asset.breaker_id is None
        assert asset.disconnect is None
        assert asset.disconnect_id is None
        assert asset.needs_compressed_air is False
        assert asset.needs_ventilation is False
        assert asset.generates_heat_or_flame is False
        assert asset.needs_chilling is False
        assert asset.special_requirements == ""
        assert asset.work_safety_notes == ""
        assert not AssetSiteRequirements.objects.filter(asset=asset).exists()

    def test_reads_reflect_existing_profile(self):
        asset = AssetFactory()
        breaker = _make_breaker(asset.location)
        AssetSiteRequirements.objects.create(
            asset=asset, breaker=breaker, needs_compressed_air=True
        )
        # Refresh to drop any cached reverse relation.
        asset.refresh_from_db()
        assert asset.breaker == breaker
        assert asset.breaker_id == breaker.id
        assert asset.needs_compressed_air is True


class TestAssetCompatWriteThrough:
    def test_bool_setter_creates_profile(self):
        asset = AssetFactory()
        asset.needs_compressed_air = True
        assert AssetSiteRequirements.objects.filter(asset=asset, needs_compressed_air=True).exists()
        # And a fresh read from the DB agrees.
        assert AssetFactory._meta.model.objects.get(pk=asset.pk).needs_compressed_air is True

    def test_breaker_setter_persists_and_reads_back(self):
        asset = AssetFactory()
        breaker = _make_breaker(asset.location)
        asset.breaker = breaker
        assert asset.breaker == breaker
        assert asset.breaker_id == breaker.id
        # Persisted on the profile, not the asset.
        profile = AssetSiteRequirements.objects.get(asset=asset)
        assert profile.breaker_id == breaker.id

    def test_setter_updates_existing_profile_without_duplicates(self):
        asset = AssetFactory()
        asset.needs_compressed_air = True
        asset.needs_ventilation = True
        asset.special_requirements = "Keep dry"
        asset.work_safety_notes = "Bleed the line first"
        assert AssetSiteRequirements.objects.filter(asset=asset).count() == 1
        profile = AssetSiteRequirements.objects.get(asset=asset)
        assert profile.needs_compressed_air is True
        assert profile.needs_ventilation is True
        assert profile.special_requirements == "Keep dry"
        assert profile.work_safety_notes == "Bleed the line first"

    def test_new_field_setters(self):
        asset = AssetFactory()
        asset.generates_heat_or_flame = True
        asset.needs_chilling = True
        profile = AssetSiteRequirements.objects.get(asset=asset)
        assert profile.generates_heat_or_flame is True
        assert profile.needs_chilling is True


class TestCircuitShim:
    def test_circuit_prefers_breaker_location(self):
        asset = AssetFactory(breaker_location="Panel A/12")
        assert asset.circuit == "Panel A/12"

    def test_circuit_falls_back_to_breaker_label(self):
        asset = AssetFactory()
        breaker = _make_breaker(asset.location, position="7")
        asset.breaker = breaker
        # No breaker_location → circuit derives from the breaker.
        assert asset.circuit == str(breaker)

    def test_circuit_empty_when_nothing_set(self):
        asset = AssetFactory()
        assert asset.circuit == ""


class TestReverseRelationShims:
    def test_powerbreaker_assets(self):
        asset = AssetFactory()
        breaker = _make_breaker(asset.location)
        asset.breaker = breaker
        other = AssetFactory()  # no breaker
        assert list(breaker.assets) == [asset]
        assert other not in breaker.assets

    def test_disconnect_assets(self):
        asset = AssetFactory()
        breaker = _make_breaker(asset.location)
        disconnect = _make_disconnect(breaker)
        asset.disconnect = disconnect
        assert list(disconnect.assets) == [asset]
