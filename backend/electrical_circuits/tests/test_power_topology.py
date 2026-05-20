"""
Tests for power topology models + data migration (oms-wwx).

Covers AC-1..AC-5 from the bead:
- AC-1: model creation + migrations apply on a clean DB.
- AC-2: data migration runs in a single Django data migration; idempotent;
  ambiguous parents flag `needs_review=True`.
- AC-3: every legacy Outlet has a corresponding PowerOutlet after migration.
- AC-4: NEMA outlet types validated via choices on the model.
- AC-5: model relationships behave as designed (cascade vs PROTECT).
"""

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import ProtectedError

import pytest

from electrical_circuits.models import (
    Breaker,
    Outlet,
    PowerBreaker,
    PowerCircuit,
    PowerOutlet,
    PowerPanel,
)
from inventory.tests.factories import LocationFactory


@pytest.mark.django_db
def test_power_panel_unique_per_location():
    loc = LocationFactory()
    PowerPanel.objects.create(location=loc, name="Sewing A")
    with pytest.raises(IntegrityError):
        PowerPanel.objects.create(location=loc, name="Sewing A")


@pytest.mark.django_db
def test_power_circuit_default_max_load_is_80_percent():
    loc = LocationFactory()
    panel = PowerPanel.objects.create(location=loc, name="Panel B")
    breaker = PowerBreaker.objects.create(panel=panel, position="2", amperage=20)
    circuit = PowerCircuit.objects.create(breaker=breaker)
    assert circuit.max_load_amps == 16  # 20 * 0.8


@pytest.mark.django_db
def test_power_circuit_explicit_max_load_preserved():
    loc = LocationFactory()
    panel = PowerPanel.objects.create(location=loc, name="Panel C")
    breaker = PowerBreaker.objects.create(panel=panel, position="4", amperage=30)
    circuit = PowerCircuit.objects.create(breaker=breaker, max_load_amps=12)
    assert circuit.max_load_amps == 12


@pytest.mark.django_db
def test_breaker_cascades_circuits_and_outlets_via_circuit_protect():
    """PowerBreaker -> PowerCircuit cascades; PowerCircuit -> PowerOutlet PROTECTs."""
    loc = LocationFactory()
    panel = PowerPanel.objects.create(location=loc, name="Cascade Test")
    breaker = PowerBreaker.objects.create(panel=panel, position="6", amperage=20)
    circuit = PowerCircuit.objects.create(breaker=breaker)
    PowerOutlet.objects.create(circuit=circuit, location=loc, label="o1")

    with pytest.raises(ProtectedError):
        circuit.delete()

    PowerOutlet.objects.all().delete()
    panel_pk = panel.pk
    breaker.delete()
    assert PowerCircuit.objects.count() == 0
    # Panel survives breaker deletion (no cascade upward).
    assert PowerPanel.objects.filter(pk=panel_pk).exists()


@pytest.mark.django_db
def test_nema_outlet_type_validated_via_full_clean():
    loc = LocationFactory()
    panel = PowerPanel.objects.create(location=loc, name="NEMA Test")
    breaker = PowerBreaker.objects.create(panel=panel, position="8", amperage=20)
    circuit = PowerCircuit.objects.create(breaker=breaker)

    valid = PowerOutlet(circuit=circuit, location=loc, outlet_type="L6-30R", label="ok")
    valid.full_clean()  # should not raise

    invalid = PowerOutlet(circuit=circuit, location=loc, outlet_type="bogus-code", label="bad")
    with pytest.raises(ValidationError):
        invalid.full_clean()


@pytest.mark.django_db
def test_power_outlet_label_uniqueness_only_when_set():
    """Empty labels should not collide; labelled outlets per location must be unique."""
    loc = LocationFactory()
    panel = PowerPanel.objects.create(location=loc, name="Label Test")
    breaker = PowerBreaker.objects.create(panel=panel, position="10", amperage=20)
    circuit = PowerCircuit.objects.create(breaker=breaker)

    PowerOutlet.objects.create(circuit=circuit, location=loc, label="")
    PowerOutlet.objects.create(circuit=circuit, location=loc, label="")  # OK

    PowerOutlet.objects.create(circuit=circuit, location=loc, label="bench-1")
    with pytest.raises(IntegrityError):
        PowerOutlet.objects.create(circuit=circuit, location=loc, label="bench-1")


@pytest.mark.django_db
def test_data_migration_maps_every_outlet_and_is_idempotent():
    """AC-2 + AC-3: every legacy Outlet → PowerOutlet; rerun is a no-op."""
    panel_room = LocationFactory()
    shop = LocationFactory()

    breaker_a = Breaker.objects.create(
        location=panel_room,
        panel="Main",
        breaker_number="2",
        amperage=20,
        voltage=120,
        poles=1,
    )
    breaker_b = Breaker.objects.create(
        location=panel_room,
        panel="Main",
        breaker_number="4/6",
        amperage=30,
        voltage=240,
        poles=2,
    )
    outlet_known = Outlet.objects.create(
        location=shop,
        identifier="bench-1",
        breaker=breaker_a,
        outlet_type="nema_5_20",
        plugged_in_notes="drill",
    )
    outlet_orphan = Outlet.objects.create(
        location=shop,
        identifier="floor-1",
        breaker=None,
        outlet_type="240v",
    )
    outlet_known_240 = Outlet.objects.create(
        location=shop,
        identifier="welder-1",
        breaker=breaker_b,
        outlet_type="nema_l6_30",
    )

    PowerOutlet.objects.all().delete()
    PowerCircuit.objects.all().delete()
    PowerBreaker.objects.all().delete()
    PowerPanel.objects.all().delete()

    from importlib import import_module

    from django.apps import apps as django_apps

    migration_module = import_module(
        "electrical_circuits.migrations." "0003_migrate_legacy_outlets_to_power_topology"
    )
    migration_module.migrate_outlets_forward(django_apps, None)

    assert PowerOutlet.objects.filter(legacy_outlet=outlet_known).exists()
    assert PowerOutlet.objects.filter(legacy_outlet=outlet_orphan).exists()
    assert PowerOutlet.objects.filter(legacy_outlet=outlet_known_240).exists()

    assert Outlet.objects.count() == PowerOutlet.objects.filter(legacy_outlet__isnull=False).count()

    po_orphan = PowerOutlet.objects.get(legacy_outlet=outlet_orphan)
    assert po_orphan.needs_review is True
    assert po_orphan.circuit.breaker.panel.name == "Unknown Panel - Migration"
    assert po_orphan.outlet_type == "OTHER"

    po_known = PowerOutlet.objects.get(legacy_outlet=outlet_known)
    assert po_known.outlet_type == "5-20R"
    assert po_known.needs_review is False
    assert po_known.circuit.breaker.position == "2"

    po_240 = PowerOutlet.objects.get(legacy_outlet=outlet_known_240)
    assert po_240.outlet_type == "L6-30R"
    assert po_240.circuit.breaker.phase == "AB"  # 2-pole

    panel_count_before = PowerPanel.objects.count()
    breaker_count_before = PowerBreaker.objects.count()
    outlet_count_before = PowerOutlet.objects.count()

    migration_module.migrate_outlets_forward(django_apps, None)

    assert PowerPanel.objects.count() == panel_count_before
    assert PowerBreaker.objects.count() == breaker_count_before
    assert PowerOutlet.objects.count() == outlet_count_before
