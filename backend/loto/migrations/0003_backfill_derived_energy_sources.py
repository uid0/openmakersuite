"""
Backfill electrical AssetEnergySource rows for every currently-cabled asset
(oms-qc3 AC-1 / AC-2 setup).

Walks every connected power cable, finds the upstream PowerPanel/PowerBreaker
chain via the existing power_chain resolver, and creates a derived
AssetEnergySource row whose ``derived_from`` points at the cable. Manual
electrical rows already in the table are left alone — they are
distinguished by ``derived_from IS NULL``.

Idempotent: duplicate runs hit the (asset, derived_from) update-or-create
key and rewrite the magnitude/isolation_point/required_devices in place.
"""

from __future__ import annotations

from django.db import migrations


def _format_isolation_point(panel, breaker) -> str:
    panel_name = (panel.name or "").strip() or f"Panel #{panel.pk}"
    position = (breaker.position or "").strip() or f"#{breaker.pk}"
    return f"{panel_name} breaker {position}"


def _format_magnitude(panel) -> str:
    voltage = getattr(panel, "voltage", None)
    return f"{voltage}V" if voltage else ""


def backfill(apps, schema_editor):
    Cable = apps.get_model("electrical_circuits", "Cable")
    PowerOutlet = apps.get_model("electrical_circuits", "PowerOutlet")
    PowerPort = apps.get_model("electrical_circuits", "PowerPort")
    AssetEnergySource = apps.get_model("loto", "AssetEnergySource")
    ContentType = apps.get_model("contenttypes", "ContentType")

    port_ct = ContentType.objects.get_for_model(PowerPort)
    outlet_ct = ContentType.objects.get_for_model(PowerOutlet)

    cables = Cable.objects.filter(cable_type="power", status="connected")
    for cable in cables:
        # Identify the PowerPort / PowerOutlet sides regardless of order.
        port_pk = None
        outlet_pk = None
        for ct_id, obj_id in (
            (cable.endpoint_a_content_type_id, cable.endpoint_a_object_id),
            (cable.endpoint_b_content_type_id, cable.endpoint_b_object_id),
        ):
            if ct_id == port_ct.id:
                try:
                    port_pk = int(obj_id)
                except (TypeError, ValueError):
                    pass
            elif ct_id == outlet_ct.id:
                try:
                    outlet_pk = int(obj_id)
                except (TypeError, ValueError):
                    pass
        if port_pk is None or outlet_pk is None:
            continue

        try:
            port = PowerPort.objects.select_related("asset").get(pk=port_pk)
        except PowerPort.DoesNotExist:
            continue
        try:
            outlet = PowerOutlet.objects.select_related(
                "circuit", "circuit__breaker", "circuit__breaker__panel"
            ).get(pk=outlet_pk)
        except PowerOutlet.DoesNotExist:
            continue

        circuit = outlet.circuit
        if circuit is None:
            continue
        breaker = circuit.breaker
        if breaker is None:
            continue
        panel = breaker.panel
        if panel is None:
            continue

        defaults = {
            "source_type": "electrical",
            "magnitude": _format_magnitude(panel),
            "isolation_point": _format_isolation_point(panel, breaker),
            "is_stale": False,
        }
        source, _created = AssetEnergySource.objects.update_or_create(
            asset=port.asset,
            derived_from=cable,
            defaults=defaults,
        )

        breaker_devices = list(breaker.required_loto_devices.all())
        source.required_devices.set(breaker_devices)


def reverse_backfill(apps, schema_editor):
    """Drop only the derived rows; manual entries (derived_from IS NULL) survive."""

    AssetEnergySource = apps.get_model("loto", "AssetEnergySource")
    AssetEnergySource.objects.filter(derived_from__isnull=False).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("loto", "0002_assetenergysource_derived_from_and_more"),
        ("electrical_circuits", "0005_powerbreaker_required_loto_devices"),
    ]

    operations = [
        migrations.RunPython(backfill, reverse_backfill),
    ]
