"""Tests for the optional Asset.mac_address field."""

from django.core.exceptions import ValidationError

import pytest

from inventory.tests.factories import AssetFactory


@pytest.mark.django_db
class TestAssetMacAddress:
    @pytest.mark.parametrize(
        "mac",
        [
            "AA:BB:CC:11:22:33",
            "aa:bb:cc:11:22:33",
            "AA-BB-CC-11-22-33",
            "0a:1B:2c:3D:4e:5F",
        ],
    )
    def test_mac_address_valid_formats_accepted(self, mac):
        asset = AssetFactory(mac_address=mac)
        asset.full_clean()
        asset.save()
        asset.refresh_from_db()
        assert asset.mac_address == mac

    @pytest.mark.parametrize(
        "mac",
        [
            "ZZ:BB:CC:11:22:33",
            "AA:BB:CC:11:22",
            "AA:BB:CC:11:22:33:44",
            "AABBCC112233",
            "GG:HH:II:JJ:KK:LL",
        ],
    )
    def test_invalid_mac_rejected(self, mac):
        asset = AssetFactory.build(mac_address=mac)
        with pytest.raises(ValidationError) as exc_info:
            asset.full_clean()
        assert "mac_address" in exc_info.value.message_dict

    def test_blank_mac_allowed(self):
        asset = AssetFactory(mac_address="")
        asset.full_clean()
        asset.save()
        asset.refresh_from_db()
        assert asset.mac_address == ""

    def test_mac_address_default_blank(self):
        asset = AssetFactory()
        assert asset.mac_address == ""


@pytest.mark.django_db
class TestAssetMacAddressSerializer:
    def test_serializer_round_trip_writes_and_reads_mac(self):
        from inventory.serializers import AssetSerializer

        asset = AssetFactory(mac_address="AA:BB:CC:11:22:33")
        data = AssetSerializer(asset).data
        assert data["mac_address"] == "AA:BB:CC:11:22:33"

        serializer = AssetSerializer(
            asset,
            data={"name": asset.name, "mac_address": "11-22-33-44-55-66"},
            partial=True,
        )
        assert serializer.is_valid(), serializer.errors
        updated = serializer.save()
        assert updated.mac_address == "11-22-33-44-55-66"

    def test_serializer_rejects_invalid_mac(self):
        from inventory.serializers import AssetSerializer

        asset = AssetFactory()
        serializer = AssetSerializer(
            asset,
            data={"mac_address": "not-a-mac"},
            partial=True,
        )
        assert not serializer.is_valid()
        assert "mac_address" in serializer.errors
