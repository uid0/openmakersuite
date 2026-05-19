"""Tests for the Thermostat model (oms-gzycmj)."""

from __future__ import annotations

import pytest

from climate.models import Thermostat
from inventory.tests.factories import AssetFactory, LocationFactory


@pytest.mark.django_db
def test_thermostat_str_uses_controls_location():
    room = LocationFactory(name="Thermo Test Shop")
    hallway = LocationFactory(name="Thermo Test Hallway")
    t = Thermostat.objects.create(
        location=hallway,
        controls_location=room,
        label="north",
    )
    assert "Thermo Test Shop" in str(t)
    assert "north" in str(t)


@pytest.mark.django_db
def test_thermostat_save_defaults_controls_location_to_location():
    """When controls_location is blank, save() copies it from location."""

    room = LocationFactory()
    t = Thermostat.objects.create(
        location=room,
        label="Office stat",
    )
    assert t.controls_location_id == room.pk


@pytest.mark.django_db
def test_thermostat_with_explicit_controls_location_preserved():
    mount = LocationFactory()
    conditioned = LocationFactory()
    t = Thermostat.objects.create(
        location=mount,
        controls_location=conditioned,
        label="north",
    )
    assert t.controls_location_id == conditioned.pk
    assert t.location_id == mount.pk


@pytest.mark.django_db
def test_controlled_asset_relation_round_trips():
    room = LocationFactory()
    asset = AssetFactory(name="RTU-3", location=room)
    t = Thermostat.objects.create(
        location=room,
        label="Stat",
        controlled_asset=asset,
    )
    t.refresh_from_db()
    assert t.controlled_asset_id == asset.pk
    assert list(asset.controlling_thermostats.all()) == [t]


@pytest.mark.django_db
def test_needs_review_defaults_false():
    room = LocationFactory()
    t = Thermostat.objects.create(location=room, label="Stat")
    assert t.needs_review is False
