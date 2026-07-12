# Data migration: copy the hazardous-material / NFPA fields off
# ``inventory.InventoryItem`` into ``inventory.InventorySafetyProfile`` (#885).
#
# Runs AFTER 0084 creates the profile model and BEFORE 0086 removes the old
# columns from InventoryItem — the field values must still be readable here.
# Modelled on facilities/migrations/0002_migrate_asset_requirements.py.

from django.db import migrations

# The 7 fields being relocated, paired with the model default that marks a
# "no data" value. A profile row is only materialised for items carrying at
# least one non-default value, so the overwhelmingly-non-hazardous catalog
# stays row-free (matching the lazy-profile design).
HAZMAT_DEFAULTS = {
    "is_hazardous": False,
    "msds_url": "",
    "msds_file": "",
    "nfpa_health_hazard": None,
    "nfpa_fire_hazard": None,
    "nfpa_instability_hazard": None,
    "nfpa_special_hazards": "",
}


def copy_hazmat_forward(apps, schema_editor):
    """Copy each item's hazmat/NFPA values to its safety profile.

    ``msds_file`` is a stored path string on the item; it is copied by plain
    value (no file is re-uploaded). Every item whose hazmat data differs from
    the field defaults gets (or updates) an ``InventorySafetyProfile`` row.
    """

    InventoryItem = apps.get_model("inventory", "InventoryItem")
    InventorySafetyProfile = apps.get_model("inventory", "InventorySafetyProfile")

    for item in InventoryItem.objects.all().iterator():
        values = {
            "is_hazardous": item.is_hazardous,
            "msds_url": item.msds_url,
            # FileField historical value: copy the stored path string as-is.
            "msds_file": item.msds_file.name if item.msds_file else "",
            "nfpa_health_hazard": item.nfpa_health_hazard,
            "nfpa_fire_hazard": item.nfpa_fire_hazard,
            "nfpa_instability_hazard": item.nfpa_instability_hazard,
            "nfpa_special_hazards": item.nfpa_special_hazards,
        }
        has_data = any(values[field] != default for field, default in HAZMAT_DEFAULTS.items())
        if has_data:
            InventorySafetyProfile.objects.update_or_create(item=item, defaults=values)


def restore_hazmat_backward(apps, schema_editor):
    """Best-effort reverse: copy profile values back onto the item columns.

    Django reverses migrations in reverse order, so 0086 (which re-adds the
    columns on reverse) has already run by the time this executes — the item
    columns exist again and can be written. Profile rows are then dropped.
    """

    InventoryItem = apps.get_model("inventory", "InventoryItem")
    InventorySafetyProfile = apps.get_model("inventory", "InventorySafetyProfile")

    for profile in InventorySafetyProfile.objects.select_related("item").iterator():
        item = profile.item
        item.is_hazardous = profile.is_hazardous
        item.msds_url = profile.msds_url
        item.msds_file = profile.msds_file.name if profile.msds_file else ""
        item.nfpa_health_hazard = profile.nfpa_health_hazard
        item.nfpa_fire_hazard = profile.nfpa_fire_hazard
        item.nfpa_instability_hazard = profile.nfpa_instability_hazard
        item.nfpa_special_hazards = profile.nfpa_special_hazards
        item.save(
            update_fields=[
                "is_hazardous",
                "msds_url",
                "msds_file",
                "nfpa_health_hazard",
                "nfpa_fire_hazard",
                "nfpa_instability_hazard",
                "nfpa_special_hazards",
            ]
        )

    InventorySafetyProfile.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0084_inventorysafetyprofile"),
    ]

    operations = [
        migrations.RunPython(
            copy_hazmat_forward,
            restore_hazmat_backward,
        ),
    ]
