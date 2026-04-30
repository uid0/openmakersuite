"""Model-level tests for the LOTO data model (oms-78j AC-1)."""

from __future__ import annotations

from django.db import IntegrityError

import pytest

from inventory.tests.factories import AssetFactory, LocationFactory
from loto.models import AssetEnergySource, LOTODevice


@pytest.fixture
def location(db):
    return LocationFactory()


@pytest.fixture
def asset(db):
    return AssetFactory()


def test_loto_device_str_includes_type_and_label(location):
    device = LOTODevice.objects.create(
        device_type=LOTODevice.DEVICE_PADLOCK,
        label="PAD-001",
        location=location,
    )
    assert str(device) == "Padlock PAD-001"
    assert device.status == LOTODevice.STATUS_AVAILABLE


def test_loto_device_label_unique_per_type(db, location):
    LOTODevice.objects.create(
        device_type=LOTODevice.DEVICE_PADLOCK,
        label="X1",
        location=location,
    )
    # Same label, different type — allowed.
    LOTODevice.objects.create(device_type=LOTODevice.DEVICE_TAG, label="X1")
    with pytest.raises(IntegrityError):
        LOTODevice.objects.create(device_type=LOTODevice.DEVICE_PADLOCK, label="X1")


def test_asset_energy_source_links_required_devices(asset):
    source = AssetEnergySource.objects.create(
        asset=asset,
        source_type=AssetEnergySource.SOURCE_ELECTRICAL,
        magnitude="240V",
        isolation_point="Panel A breaker 12",
    )
    pad = LOTODevice.objects.create(device_type=LOTODevice.DEVICE_PADLOCK, label="P1")
    breaker_lock = LOTODevice.objects.create(device_type=LOTODevice.DEVICE_BREAKER_LOCK, label="B1")
    source.required_devices.add(pad, breaker_lock)

    assert source.required_devices.count() == 2
    # Reverse relation works for a fast lookup of "what does this device serve?"
    assert pad.energy_sources.first() == source


def test_asset_energy_source_str_renders_magnitude(asset):
    source = AssetEnergySource.objects.create(
        asset=asset,
        source_type=AssetEnergySource.SOURCE_PNEUMATIC,
        magnitude="80psi",
    )
    assert "Pneumatic" in str(source)
    assert "80psi" in str(source)


def test_asset_can_have_multiple_energy_sources(asset):
    AssetEnergySource.objects.create(asset=asset, source_type=AssetEnergySource.SOURCE_ELECTRICAL)
    AssetEnergySource.objects.create(asset=asset, source_type=AssetEnergySource.SOURCE_PNEUMATIC)
    assert asset.energy_sources.count() == 2


def test_loto_device_default_status_available(db):
    device = LOTODevice.objects.create(device_type=LOTODevice.DEVICE_TAG, label="TAG-1")
    assert device.status == LOTODevice.STATUS_AVAILABLE
