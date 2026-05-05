"""Backfill AssetEnergySource rows from existing connected PowerCables (oms-qc3).

For every CONNECTED power cable that pairs a PowerOutlet with a PowerPort,
create one derived AssetEnergySource for the asset on the port end. The
derivation logic mirrors :mod:`loto.services.derivation` but is reimplemented
inline to stay frozen against schema changes — historical migrations may not
import live model classes.
"""

from __future__ import annotations

from django.db import migrations


def _format_magnitude(panel_voltage: int, breaker_amperage: int) -> str:
    return f"{panel_voltage}V {breaker_amperage}A"


def _format_isolation_point(panel_name: str, breaker_position: str) -> str:
    return f"{panel_name} Breaker {breaker_position}"


def backfill(apps, schema_editor):
    Cable = apps.get_model("electrical_circuits", "Cable")
    PowerOutlet = apps.get_model("electrical_circuits", "PowerOutlet")
    PowerPort = apps.get_model("electrical_circuits", "PowerPort")
    AssetEnergySource = apps.get_model("loto", "AssetEnergySource")
    ContentType = apps.get_model("contenttypes", "ContentType")

    outlet_ct = ContentType.objects.get_for_model(PowerOutlet)
    port_ct = ContentType.objects.get_for_model(PowerPort)

    cables = Cable.objects.filter(
        cable_type="power",
        status="connected",
    )

    for cable in cables:
        a_ct = cable.endpoint_a_content_type_id
        b_ct = cable.endpoint_b_content_type_id

        if a_ct == outlet_ct.id and b_ct == port_ct.id:
            outlet_id, port_id = cable.endpoint_a_object_id, cable.endpoint_b_object_id
        elif b_ct == outlet_ct.id and a_ct == port_ct.id:
            outlet_id, port_id = cable.endpoint_b_object_id, cable.endpoint_a_object_id
        else:
            continue

        outlet = PowerOutlet.objects.filter(pk=outlet_id).select_related(
            "circuit__breaker__panel"
        ).first()
        port = PowerPort.objects.filter(pk=port_id).select_related("asset").first()
        if outlet is None or port is None:
            continue

        breaker = outlet.circuit.breaker
        panel = breaker.panel

        source, _created = AssetEnergySource.objects.update_or_create(
            derived_from=cable,
            defaults={
                "asset": port.asset,
                "source_type": "electrical",
                "magnitude": _format_magnitude(panel.voltage, breaker.amperage),
                "isolation_point": _format_isolation_point(panel.name, breaker.position),
                "status": "active",
            },
        )
        # Pull required_devices from the breaker's lockout_devices m2m
        # (the reverse side of LOTODevice.secured_breakers).
        device_ids = list(breaker.lockout_devices.values_list("pk", flat=True))
        if device_ids:
            source.required_devices.set(device_ids)


def unbackfill(apps, schema_editor):
    AssetEnergySource = apps.get_model("loto", "AssetEnergySource")
    AssetEnergySource.objects.filter(derived_from__isnull=False).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("loto", "0002_assetenergysource_derived_from_and_more"),
        ("electrical_circuits", "0004_cable"),
    ]

    operations = [
        migrations.RunPython(backfill, reverse_code=unbackfill),
    ]
