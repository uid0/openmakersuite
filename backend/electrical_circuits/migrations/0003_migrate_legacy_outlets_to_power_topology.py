"""
Data migration: legacy `Breaker` + `Outlet` rows → power topology models.

Idempotent. Re-running is a no-op because:
- PowerPanel is keyed on (location, name) and `get_or_create`'d.
- PowerBreaker is keyed on (panel, position) and `get_or_create`'d.
- PowerCircuit is created at most once per legacy breaker (skipped if any
  exist).
- PowerOutlet uses the OneToOne `legacy_outlet` link to skip rows already
  migrated.

Records with ambiguous parentage (Outlet.breaker is null, or outlet_type does
not map cleanly to a NEMA code) get `needs_review=True` so admins can fix them.
"""

from django.db import migrations

# Old electrical_circuits.Outlet.outlet_type → new NEMA standard code.
# Anything not in this map (or "240v" / "standard" with no extra hint) lands as
# OTHER + needs_review=True for admin follow-up.
OUTLET_TYPE_MAP = {
    "standard": ("5-15R", False),
    "nema_5_15": ("5-15R", False),
    "nema_5_20": ("5-20R", False),
    "nema_6_15": ("6-15R", False),
    "nema_6_20": ("6-20R", False),
    "nema_l6_30": ("L6-30R", False),
    "nema_14_30": ("14-30R", False),
    "nema_14_50": ("14-50R", False),
    "usb": ("USB", False),
    "240v": ("OTHER", True),
    "other": ("OTHER", True),
}

PLACEHOLDER_PANEL_NAME = "Unknown Panel - Migration"
PLACEHOLDER_BREAKER_POSITION = "?"


def migrate_outlets_forward(apps, schema_editor):
    Breaker = apps.get_model("electrical_circuits", "Breaker")
    Outlet = apps.get_model("electrical_circuits", "Outlet")
    PowerPanel = apps.get_model("electrical_circuits", "PowerPanel")
    PowerBreaker = apps.get_model("electrical_circuits", "PowerBreaker")
    PowerCircuit = apps.get_model("electrical_circuits", "PowerCircuit")
    PowerOutlet = apps.get_model("electrical_circuits", "PowerOutlet")

    panel_for_legacy = {}
    breaker_for_legacy = {}
    circuit_for_legacy_breaker = {}

    for legacy_breaker in Breaker.objects.all():
        phase_config = (
            "three"
            if legacy_breaker.poles == 3
            else "split" if legacy_breaker.voltage >= 240 else "single"
        )
        panel, _ = PowerPanel.objects.get_or_create(
            location_id=legacy_breaker.location_id,
            name=legacy_breaker.panel,
            defaults={
                "phase_configuration": phase_config,
                "voltage": legacy_breaker.voltage,
            },
        )
        panel_for_legacy[(legacy_breaker.location_id, legacy_breaker.panel)] = panel

        phase_value = (
            "ABC" if legacy_breaker.poles == 3 else "AB" if legacy_breaker.poles == 2 else "A"
        )
        new_breaker, _ = PowerBreaker.objects.get_or_create(
            panel=panel,
            position=legacy_breaker.breaker_number,
            defaults={
                "pole_count": min(legacy_breaker.poles, 3),
                "amperage": legacy_breaker.amperage,
                "phase": phase_value,
                "status": "active" if legacy_breaker.is_active else "locked_out",
                "label": legacy_breaker.description,
                "notes": legacy_breaker.notes,
            },
        )
        breaker_for_legacy[legacy_breaker.id] = new_breaker

        circuit = new_breaker.circuits.first()
        if circuit is None:
            circuit = PowerCircuit.objects.create(
                breaker=new_breaker,
                label=legacy_breaker.description or "",
                max_load_amps=int(legacy_breaker.amperage * 0.8),
            )
        circuit_for_legacy_breaker[legacy_breaker.id] = circuit

    placeholder_circuit_for_location = {}

    def get_placeholder_circuit(location_id):
        if location_id in placeholder_circuit_for_location:
            return placeholder_circuit_for_location[location_id]
        panel, _ = PowerPanel.objects.get_or_create(
            location_id=location_id,
            name=PLACEHOLDER_PANEL_NAME,
            defaults={
                "phase_configuration": "single",
                "voltage": 120,
                "needs_review": True,
                "notes": "Auto-created during oms-wwx migration for outlets with no known breaker.",
            },
        )
        breaker, _ = PowerBreaker.objects.get_or_create(
            panel=panel,
            position=PLACEHOLDER_BREAKER_POSITION,
            defaults={
                "pole_count": 1,
                "amperage": 20,
                "phase": "A",
                "status": "active",
                "needs_review": True,
            },
        )
        circuit = breaker.circuits.first()
        if circuit is None:
            circuit = PowerCircuit.objects.create(
                breaker=breaker,
                label="Migration placeholder",
                max_load_amps=16,
                needs_review=True,
            )
        placeholder_circuit_for_location[location_id] = circuit
        return circuit

    for outlet in Outlet.objects.all():
        if PowerOutlet.objects.filter(legacy_outlet_id=outlet.id).exists():
            continue

        nema_code, type_review = OUTLET_TYPE_MAP.get(outlet.outlet_type, ("OTHER", True))

        if outlet.breaker_id is not None and outlet.breaker_id in circuit_for_legacy_breaker:
            circuit = circuit_for_legacy_breaker[outlet.breaker_id]
            parent_review = False
        else:
            circuit = get_placeholder_circuit(outlet.location_id)
            parent_review = True

        PowerOutlet.objects.create(
            circuit=circuit,
            location_id=outlet.location_id,
            outlet_type=nema_code,
            label=outlet.identifier,
            location_description=outlet.description,
            status="active" if outlet.is_active else "inactive",
            notes=outlet.plugged_in_notes,
            needs_review=parent_review or type_review,
            legacy_outlet_id=outlet.id,
        )


def migrate_outlets_reverse(apps, schema_editor):
    PowerOutlet = apps.get_model("electrical_circuits", "PowerOutlet")
    PowerCircuit = apps.get_model("electrical_circuits", "PowerCircuit")
    PowerBreaker = apps.get_model("electrical_circuits", "PowerBreaker")
    PowerPanel = apps.get_model("electrical_circuits", "PowerPanel")

    PowerOutlet.objects.filter(legacy_outlet__isnull=False).delete()
    PowerCircuit.objects.filter(outlets__isnull=True).delete()
    PowerBreaker.objects.filter(circuits__isnull=True).delete()
    PowerPanel.objects.filter(breakers__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("electrical_circuits", "0002_powerbreaker_powercircuit_poweroutlet_powerpanel_and_more"),
    ]

    operations = [
        migrations.RunPython(migrate_outlets_forward, migrate_outlets_reverse),
    ]
