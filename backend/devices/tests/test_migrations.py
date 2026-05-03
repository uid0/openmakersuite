"""Data-migration tests for the devices app (oms-06x).

Covers:

* AC-2 — ESP32Device.mac_address remains accessible after backfill.
* AC-5 — backfill handles mixed MAC formats (colon, dash, mixed case).
* The legacy NetworkDrop copy migration is idempotent and field-mapped.

The data migrations have already run by the time these tests start, but they
operate against rows seeded in this test session — so we exercise them
explicitly by calling the migration callables a second time. They must be
idempotent.
"""

from importlib import import_module

from django.apps import apps as django_apps

import pytest

from devices.models import Interface, NetworkDrop
from electrical_circuits.models import NetworkDrop as LegacyNetworkDrop
from forgekey.models import AssetDevice, ESP32Device
from forgekey.tests.factories import DeviceTypeFactory
from inventory.tests.factories import AssetFactory, LocationFactory

backfill_mod = import_module("devices.migrations.0002_backfill_interfaces")
copy_drops_mod = import_module("devices.migrations.0003_copy_network_drops")


# --------------------------------------------------------------------------- #
# AC-5: mixed-format MAC migration
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_backfill_handles_mixed_mac_formats():
    device_type = DeviceTypeFactory()
    a1 = AssetFactory(asset_tag="DMS-MIX-001")
    a2 = AssetFactory(asset_tag="DMS-MIX-002")
    a3 = AssetFactory(asset_tag="DMS-MIX-003")
    a4 = AssetFactory(asset_tag="DMS-MIX-004")

    # Three ESP32 devices with progressively quirkier MAC formats.
    d_colon = ESP32Device.objects.create(
        mac_address="AA:BB:CC:11:22:33",
        device_type=device_type,
        name="Colon device",
    )
    d_dash = ESP32Device.objects.create(
        mac_address="AA-BB-CC-11-22-44",
        device_type=device_type,
        name="Dash device",
    )
    d_mixed = ESP32Device.objects.create(
        mac_address="aA:Bb:cC:11:22:55",
        device_type=device_type,
        name="Mixed-case device",
    )
    AssetDevice.objects.create(asset=a1, device=d_colon, is_primary=True)
    AssetDevice.objects.create(asset=a2, device=d_dash, is_primary=True)
    AssetDevice.objects.create(asset=a3, device=d_mixed, is_primary=True)

    # An Asset whose own mac_address field is set in colon form.
    a4.mac_address = "AA:BB:CC:11:22:66"
    a4.save()

    backfill_mod.backfill_interfaces(django_apps, None)

    by_asset = {iface.asset_id: iface for iface in Interface.objects.all()}
    assert by_asset[a1.id].mac_address == "aabbcc112233"
    assert by_asset[a1.id].interface_type == "wifi"
    assert by_asset[a1.id].name == "wlan0"
    assert by_asset[a2.id].mac_address == "aabbcc112244"
    assert by_asset[a3.id].mac_address == "aabbcc112255"

    a4_iface = by_asset[a4.id]
    assert a4_iface.mac_address == "aabbcc112266"
    assert a4_iface.interface_type == "ethernet"
    assert a4_iface.name == "eth0"


# --------------------------------------------------------------------------- #
# AC-2: ESP32Device.mac_address back-compat
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_esp32device_mac_address_field_survives_backfill():
    """The denormalized mac_address column on ESP32Device is the back-compat
    surface for existing callers (per AC-2). Backfill must not drop it.
    """

    device_type = DeviceTypeFactory()
    asset = AssetFactory()
    device = ESP32Device.objects.create(
        mac_address="DE:AD:BE:EF:00:01",
        device_type=device_type,
        name="Soak test",
    )
    AssetDevice.objects.create(asset=asset, device=device, is_primary=True)

    backfill_mod.backfill_interfaces(django_apps, None)

    device.refresh_from_db()
    # Original column unchanged (preserves whatever format admins entered).
    assert device.mac_address == "DE:AD:BE:EF:00:01"
    # And there's a corresponding canonical Interface.
    iface = Interface.objects.get(asset=asset, mac_address="deadbeef0001")
    assert iface.interface_type == "wifi"


# --------------------------------------------------------------------------- #
# Idempotency + edge cases
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_backfill_is_idempotent():
    device_type = DeviceTypeFactory()
    asset = AssetFactory()
    device = ESP32Device.objects.create(
        mac_address="01:02:03:04:05:06",
        device_type=device_type,
    )
    AssetDevice.objects.create(asset=asset, device=device, is_primary=True)

    backfill_mod.backfill_interfaces(django_apps, None)
    backfill_mod.backfill_interfaces(django_apps, None)

    assert Interface.objects.filter(asset=asset).count() == 1


@pytest.mark.django_db
def test_backfill_skips_devices_without_asset_link():
    device_type = DeviceTypeFactory()
    ESP32Device.objects.create(
        mac_address="11:22:33:44:55:66",
        device_type=device_type,
    )

    backfill_mod.backfill_interfaces(django_apps, None)

    assert Interface.objects.count() == 0


@pytest.mark.django_db
def test_backfill_prefers_primary_asset_link():
    device_type = DeviceTypeFactory()
    primary_asset = AssetFactory(asset_tag="DMS-PRIM-001")
    secondary_asset = AssetFactory(asset_tag="DMS-PRIM-002")
    device = ESP32Device.objects.create(
        mac_address="aa:11:22:33:44:55",
        device_type=device_type,
    )
    # Non-primary link created first; the migration must still pick the primary.
    AssetDevice.objects.create(asset=secondary_asset, device=device, is_primary=False)
    AssetDevice.objects.create(asset=primary_asset, device=device, is_primary=True)

    backfill_mod.backfill_interfaces(django_apps, None)

    assert Interface.objects.filter(asset=primary_asset).count() == 1
    assert Interface.objects.filter(asset=secondary_asset).count() == 0


@pytest.mark.django_db
def test_backfill_avoids_name_collision_with_existing_interface():
    """If an admin has already created (e.g.) wlan0 manually with a different
    MAC, the backfill picks a non-colliding name suffix instead of crashing
    on the unique-(asset, name) constraint.
    """

    device_type = DeviceTypeFactory()
    asset = AssetFactory()
    Interface.objects.create(
        asset=asset,
        name="wlan0",
        interface_type=Interface.TYPE_WIFI,
        mac_address="ff:ff:ff:ff:ff:fe",
    )
    device = ESP32Device.objects.create(
        mac_address="aa:bb:cc:dd:ee:01",
        device_type=device_type,
    )
    AssetDevice.objects.create(asset=asset, device=device, is_primary=True)

    backfill_mod.backfill_interfaces(django_apps, None)

    new_iface = Interface.objects.get(asset=asset, mac_address="aabbccddee01")
    assert new_iface.name != "wlan0"
    assert new_iface.name.startswith("wlan0")


# --------------------------------------------------------------------------- #
# NetworkDrop copy migration
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_legacy_network_drop_copy():
    closet = LocationFactory()
    LegacyNetworkDrop.objects.create(
        location=closet,
        identifier="rack-1-port-24",
        drop_type="patch_panel",
        patch_panel="MDF Panel B",
        patch_port="24",
        notes="copper jack",
    )
    LegacyNetworkDrop.objects.create(
        location=closet,
        identifier="ap-east",
        drop_type="ap",
        notes="",
    )

    copy_drops_mod.copy_drops(django_apps, None)

    rack = NetworkDrop.objects.get(location=closet, drop_label="rack-1-port-24")
    assert rack.patch_panel_position == "MDF Panel B / 24"
    assert rack.notes == "copper jack"
    assert rack.wire_type == "cat6"

    ap = NetworkDrop.objects.get(location=closet, drop_label="ap-east")
    assert ap.patch_panel_position == ""

    # Idempotent: a second run does not duplicate or error.
    copy_drops_mod.copy_drops(django_apps, None)
    assert NetworkDrop.objects.filter(location=closet).count() == 2
