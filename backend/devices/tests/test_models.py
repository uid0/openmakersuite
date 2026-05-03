"""Model-level tests for devices.Interface and devices.NetworkDrop (oms-06x)."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError

import pytest

from devices.models import Interface, NetworkDrop, normalize_mac
from inventory.tests.factories import AssetFactory, LocationFactory

# --------------------------------------------------------------------------- #
# AC-4: MAC validation + normalization
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("AA:BB:CC:DD:EE:FF", "aabbccddeeff"),
        ("aa:bb:cc:dd:ee:ff", "aabbccddeeff"),
        ("AA-BB-CC-DD-EE-FF", "aabbccddeeff"),
        ("aabbccddeeff", "aabbccddeeff"),
        ("Aa:Bb:Cc:Dd:Ee:Ff", "aabbccddeeff"),
    ],
)
def test_interface_save_normalizes_mac(raw, expected):
    asset = AssetFactory()
    iface = Interface.objects.create(
        asset=asset,
        name=f"eth-{expected[:4]}",
        interface_type=Interface.TYPE_ETHERNET,
        mac_address=raw,
    )
    iface.refresh_from_db()
    assert iface.mac_address == expected


@pytest.mark.django_db
@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not-a-mac",
        "aabbccddeef",  # 11 hex digits
        "aabbccddeeffaa",  # 14 hex digits
        "ZZ:BB:CC:DD:EE:FF",
        "aa::bb:cc:dd:ee:ff",
    ],
)
def test_interface_full_clean_rejects_invalid_mac(bad):
    asset = AssetFactory()
    iface = Interface(
        asset=asset,
        name="eth0",
        interface_type=Interface.TYPE_ETHERNET,
        mac_address=bad,
    )
    with pytest.raises(ValidationError):
        iface.full_clean()


@pytest.mark.django_db
def test_normalize_mac_helper_rejects_garbage():
    with pytest.raises(ValidationError):
        normalize_mac("not even close")


# --------------------------------------------------------------------------- #
# AC-3: An asset can have N>1 interfaces; uniqueness is per-asset
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_asset_can_have_multiple_interfaces():
    asset = AssetFactory()
    Interface.objects.create(
        asset=asset,
        name="eth0",
        interface_type=Interface.TYPE_ETHERNET,
        mac_address="aa:bb:cc:dd:ee:01",
    )
    Interface.objects.create(
        asset=asset,
        name="wlan0",
        interface_type=Interface.TYPE_WIFI,
        mac_address="aa:bb:cc:dd:ee:02",
    )
    assert asset.interfaces.count() == 2
    assert {i.interface_type for i in asset.interfaces.all()} == {
        Interface.TYPE_ETHERNET,
        Interface.TYPE_WIFI,
    }


@pytest.mark.django_db
def test_interface_unique_mac_within_asset():
    asset = AssetFactory()
    Interface.objects.create(
        asset=asset,
        name="eth0",
        interface_type=Interface.TYPE_ETHERNET,
        mac_address="aa:bb:cc:dd:ee:ff",
    )
    with pytest.raises(IntegrityError):
        Interface.objects.create(
            asset=asset,
            name="eth1",
            interface_type=Interface.TYPE_ETHERNET,
            mac_address="AA-BB-CC-DD-EE-FF",  # same MAC after normalization
        )


@pytest.mark.django_db
def test_interface_same_mac_allowed_on_different_assets():
    a1 = AssetFactory(asset_tag="DMS-DIFF-001")
    a2 = AssetFactory(asset_tag="DMS-DIFF-002")
    Interface.objects.create(
        asset=a1,
        name="eth0",
        interface_type=Interface.TYPE_ETHERNET,
        mac_address="aabbccddeeff",
    )
    # Different asset, same MAC: legal (uniqueness is per-asset).
    Interface.objects.create(
        asset=a2,
        name="eth0",
        interface_type=Interface.TYPE_ETHERNET,
        mac_address="aabbccddeeff",
    )
    assert Interface.objects.filter(mac_address="aabbccddeeff").count() == 2


@pytest.mark.django_db
def test_interface_unique_name_within_asset():
    asset = AssetFactory()
    Interface.objects.create(
        asset=asset,
        name="eth0",
        interface_type=Interface.TYPE_ETHERNET,
        mac_address="aa:bb:cc:dd:ee:01",
    )
    with pytest.raises(IntegrityError):
        Interface.objects.create(
            asset=asset,
            name="eth0",
            interface_type=Interface.TYPE_ETHERNET,
            mac_address="aa:bb:cc:dd:ee:02",
        )


# --------------------------------------------------------------------------- #
# Interface basics
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_interface_str_uses_normalized_mac():
    asset = AssetFactory(name="Sewing Machine 1")
    iface = Interface.objects.create(
        asset=asset,
        name="wlan0",
        interface_type=Interface.TYPE_WIFI,
        mac_address="AA:BB:CC:DD:EE:FF",
    )
    assert "Sewing Machine 1" in str(iface)
    assert "wlan0" in str(iface)
    assert "aabbccddeeff" in str(iface)


@pytest.mark.django_db
def test_interface_optional_fields_have_sensible_defaults():
    asset = AssetFactory()
    iface = Interface.objects.create(
        asset=asset,
        name="eth0",
        interface_type=Interface.TYPE_ETHERNET,
        mac_address="aa:bb:cc:dd:ee:ff",
    )
    assert iface.enabled is True
    assert iface.mtu is None
    assert iface.description == ""


# --------------------------------------------------------------------------- #
# NetworkDrop
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_networkdrop_str_and_unique_label_per_location():
    loc = LocationFactory()
    drop = NetworkDrop.objects.create(
        location=loc,
        drop_label="Sewing-NW-1",
        wire_type=NetworkDrop.WIRE_CAT6,
        patch_panel_position="IDF-1 / 24",
    )
    assert "Sewing-NW-1" in str(drop)
    assert loc.name in str(drop)

    with pytest.raises(IntegrityError):
        NetworkDrop.objects.create(
            location=loc,
            drop_label="Sewing-NW-1",
            wire_type=NetworkDrop.WIRE_CAT6,
        )


@pytest.mark.django_db
def test_networkdrop_same_label_allowed_in_different_locations():
    a, b = LocationFactory(), LocationFactory()
    NetworkDrop.objects.create(location=a, drop_label="A12")
    NetworkDrop.objects.create(location=b, drop_label="A12")
    assert NetworkDrop.objects.filter(drop_label="A12").count() == 2
