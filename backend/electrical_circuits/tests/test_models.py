"""Model-level tests for electrical_circuits (oms-tt5)."""

from django.db import IntegrityError
from django.db.models import ProtectedError

import pytest

from electrical_circuits.models import Breaker, LightSwitch, NetworkDrop, Outlet
from inventory.tests.factories import LocationFactory


@pytest.mark.django_db
def test_breaker_str_and_unique_slot():
    panel_room = LocationFactory()
    b = Breaker.objects.create(
        location=panel_room,
        panel="Panel A",
        breaker_number="12",
        amperage=20,
    )
    assert str(b) == f"Panel A / 12 (20A) @ {panel_room.name}"

    with pytest.raises(IntegrityError):
        Breaker.objects.create(
            location=panel_room,
            panel="Panel A",
            breaker_number="12",
            amperage=15,
        )


@pytest.mark.django_db
def test_outlet_unique_within_location_and_breaker_link():
    shop = LocationFactory()
    panel_room = LocationFactory()
    breaker = Breaker.objects.create(
        location=panel_room, panel="A", breaker_number="14", amperage=20
    )
    outlet = Outlet.objects.create(
        location=shop,
        identifier="bench-1",
        breaker=breaker,
        outlet_type="nema_5_20",
        plugged_in_notes="Drill press",
    )
    assert outlet.breaker == breaker
    assert breaker.outlets.count() == 1

    with pytest.raises(IntegrityError):
        Outlet.objects.create(location=shop, identifier="bench-1")


@pytest.mark.django_db
def test_outlet_breaker_protect_on_delete():
    shop = LocationFactory()
    panel_room = LocationFactory()
    breaker = Breaker.objects.create(
        location=panel_room, panel="A", breaker_number="1", amperage=20
    )
    Outlet.objects.create(location=shop, identifier="o1", breaker=breaker)
    with pytest.raises(ProtectedError):
        breaker.delete()


@pytest.mark.django_db
def test_lightswitch_controls_other_location():
    hallway = LocationFactory()
    woodshop = LocationFactory()
    sw = LightSwitch.objects.create(
        location=hallway,
        identifier="east-door",
        controls_location=woodshop,
    )
    assert sw.controls_location == woodshop
    assert woodshop.controlling_switches.first() == sw
    woodshop.delete()
    sw.refresh_from_db()
    assert sw.controls_location is None


@pytest.mark.django_db
def test_networkdrop_str_and_filtering():
    closet = LocationFactory()
    drop = NetworkDrop.objects.create(
        location=closet,
        identifier="rack-1-port-24",
        drop_type="patch_panel",
        patch_panel="MDF Panel B",
        patch_port="24",
    )
    assert "Patch panel termination" in str(drop)
    assert NetworkDrop.objects.filter(drop_type="patch_panel").count() == 1
