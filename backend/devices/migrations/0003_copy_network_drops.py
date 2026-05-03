"""Copy ``electrical_circuits.NetworkDrop`` rows into ``devices.NetworkDrop``.

The legacy model stays in place — frontend still consumes its REST endpoint
and a number of fields on it (drop_type, mac_address, ip_address, photo)
have no equivalent in the new schema. Future cleanup work will retire the
legacy model once the frontend cuts over.

Field mapping:

* ``identifier``                       → ``drop_label``
* ``patch_panel`` + ``patch_port``     → ``patch_panel_position`` (joined)
* ``notes``                            → ``notes``
* (no source)                          → ``wire_type='cat6'`` (default)

Idempotent: skips drops that already exist on the target side.
"""

from django.db import migrations


def copy_drops(apps, schema_editor):
    LegacyDrop = apps.get_model("electrical_circuits", "NetworkDrop")
    NewDrop = apps.get_model("devices", "NetworkDrop")

    for old in LegacyDrop.objects.all():
        position_parts = [p for p in (old.patch_panel, old.patch_port) if p]
        position = " / ".join(position_parts)[:120]

        NewDrop.objects.get_or_create(
            location=old.location,
            drop_label=old.identifier,
            defaults={
                "wire_type": "cat6",
                "patch_panel_position": position,
                "notes": old.notes,
            },
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0002_backfill_interfaces"),
        ("electrical_circuits", "0003_migrate_legacy_outlets_to_power_topology"),
    ]

    operations = [
        migrations.RunPython(copy_drops, noop),
    ]
