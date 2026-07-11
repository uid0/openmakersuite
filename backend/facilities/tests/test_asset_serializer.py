"""AssetSerializer round-trip tests for the #880 site-requirements refactor.

The moved fields must keep their historical JSON keys + shapes (so the SPA and
ScanTTY need no change), and the new fields must be additive. Reads come from
the profile via the Asset compat properties; writes are routed into the profile
by the serializer's ``create`` / ``update``.
"""

import pytest

from electrical_circuits.models import Disconnect, PowerBreaker, PowerCircuit, PowerPanel
from facilities.models import AssetSiteRequirements
from inventory.models import Asset
from inventory.serializers import AssetSerializer
from inventory.tests.factories import AssetFactory

pytestmark = pytest.mark.django_db


def _make_breaker(location):
    panel = PowerPanel.objects.create(location=location, name="Panel A")
    return PowerBreaker.objects.create(panel=panel, position="12", amperage=20)


def _make_disconnect(breaker):
    circuit = PowerCircuit.objects.create(breaker=breaker)
    return Disconnect.objects.create(
        circuit=circuit, label="D1", disconnect_type=Disconnect.DISCONNECT_TYPE_UNFUSED
    )


class TestAssetSerializerRead:
    def test_moved_keys_keep_their_shapes(self):
        asset = AssetFactory()
        breaker = _make_breaker(asset.location)
        disconnect = _make_disconnect(breaker)
        asset.breaker = breaker
        asset.disconnect = disconnect
        asset.needs_compressed_air = True

        data = AssetSerializer(asset).data
        # Same PK / bool shapes as before the refactor.
        assert data["breaker"] == breaker.id
        assert data["disconnect"] == disconnect.id
        assert data["needs_compressed_air"] is True
        assert data["needs_ventilation"] is False
        # circuit is now a read-only breaker-derived label.
        assert data["circuit"] == str(breaker)

    def test_new_keys_present_and_additive(self):
        asset = AssetFactory(
            generates_heat_or_flame=True,
            needs_chilling=True,
            special_requirements="Keep dry",
            work_safety_notes="Bleed the air line first",
        )
        data = AssetSerializer(asset).data
        assert data["generates_heat_or_flame"] is True
        assert data["needs_chilling"] is True
        assert data["special_requirements"] == "Keep dry"
        assert data["work_safety_notes"] == "Bleed the air line first"

    def test_defaults_without_profile(self):
        asset = AssetFactory()
        data = AssetSerializer(asset).data
        assert data["breaker"] is None
        assert data["needs_compressed_air"] is False
        assert data["generates_heat_or_flame"] is False
        assert data["special_requirements"] == ""
        assert data["circuit"] == ""


class TestAssetSerializerWrite:
    def test_create_routes_into_profile(self):
        location_asset = AssetFactory()
        breaker = _make_breaker(location_asset.location)
        serializer = AssetSerializer(
            data={
                "name": "Compressor",
                "breaker": breaker.id,
                "needs_compressed_air": True,
                "generates_heat_or_flame": True,
                "special_requirements": "220V only",
                "work_safety_notes": "Lock out at Panel A",
                # circuit is read-only — must be ignored on write.
                "circuit": "should-be-ignored",
            }
        )
        assert serializer.is_valid(), serializer.errors
        asset = serializer.save()

        profile = AssetSiteRequirements.objects.get(asset=asset)
        assert profile.breaker_id == breaker.id
        assert profile.needs_compressed_air is True
        assert profile.generates_heat_or_flame is True
        assert profile.special_requirements == "220V only"
        assert profile.work_safety_notes == "Lock out at Panel A"
        # circuit stayed read-only; derived from the breaker, not the input.
        assert asset.circuit == str(breaker)

    def test_create_without_requirements_makes_no_profile_row(self):
        serializer = AssetSerializer(data={"name": "Plain"})
        assert serializer.is_valid(), serializer.errors
        asset = serializer.save()
        assert not AssetSiteRequirements.objects.filter(asset=asset).exists()

    def test_update_upserts_profile(self):
        asset = AssetFactory()
        breaker = _make_breaker(asset.location)
        serializer = AssetSerializer(
            asset,
            data={
                "breaker": breaker.id,
                "needs_ventilation": True,
                "needs_chilling": True,
            },
            partial=True,
        )
        assert serializer.is_valid(), serializer.errors
        serializer.save()

        asset.refresh_from_db()
        assert asset.breaker_id == breaker.id
        assert asset.needs_ventilation is True
        assert asset.needs_chilling is True
        # Reloading the Asset from scratch also sees it.
        assert Asset.objects.get(pk=asset.pk).needs_ventilation is True

    def test_update_clears_breaker_with_null(self):
        asset = AssetFactory()
        breaker = _make_breaker(asset.location)
        asset.breaker = breaker
        serializer = AssetSerializer(asset, data={"breaker": None}, partial=True)
        assert serializer.is_valid(), serializer.errors
        serializer.save()
        asset.refresh_from_db()
        assert asset.breaker is None
