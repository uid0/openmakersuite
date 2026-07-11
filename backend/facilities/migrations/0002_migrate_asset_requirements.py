# Data migration: copy asset operational/site requirements off inventory.Asset
# into facilities.AssetSiteRequirements (issue #880).
#
# Runs AFTER facilities.0001 creates the model and BEFORE inventory.0083
# removes the old fields — the field values must still be readable here.
# Modelled on inventory/migrations/0030_migrate_to_forgekey.py.

from django.db import migrations


def copy_requirements_forward(apps, schema_editor):
    """Copy each asset's electrical + mechanical requirements to its profile.

    For every asset with a non-default ``breaker``/``disconnect``/
    ``needs_compressed_air``/``needs_ventilation``, upsert its
    ``AssetSiteRequirements`` row copying those values.

    The legacy free-text ``circuit`` string is consolidated separately: when
    the asset has no breaker and a blank ``breaker_location``, the circuit text
    is the only electrical-location hint we have, so fold it into
    ``breaker_location``. Otherwise the free-text circuit is redundant with the
    breaker FK / breaker_location and is dropped.
    """

    Asset = apps.get_model("inventory", "Asset")
    AssetSiteRequirements = apps.get_model("facilities", "AssetSiteRequirements")

    for asset in Asset.objects.all():
        breaker_id = asset.breaker_id
        disconnect_id = asset.disconnect_id
        needs_compressed_air = asset.needs_compressed_air
        needs_ventilation = asset.needs_ventilation
        circuit = (asset.circuit or "").strip()

        # Consolidate the legacy circuit string before deciding what to keep.
        if circuit and not breaker_id and not (asset.breaker_location or "").strip():
            asset.breaker_location = circuit
            asset.save(update_fields=["breaker_location"])

        has_profile_data = bool(
            breaker_id or disconnect_id or needs_compressed_air or needs_ventilation
        )
        if has_profile_data:
            AssetSiteRequirements.objects.update_or_create(
                asset=asset,
                defaults={
                    "breaker_id": breaker_id,
                    "disconnect_id": disconnect_id,
                    "needs_compressed_air": needs_compressed_air,
                    "needs_ventilation": needs_ventilation,
                },
            )


def restore_requirements_backward(apps, schema_editor):
    """Best-effort reverse: copy profile values back onto the Asset fields.

    Django reverses migrations in reverse order, so inventory.0083 (which
    re-adds the fields on reverse) has already run by the time this executes —
    the Asset fields exist again and can be written. The legacy free-text
    ``circuit`` consolidation is not un-done (the original distinction was
    lossy); ``circuit`` is left blank.
    """

    Asset = apps.get_model("inventory", "Asset")
    AssetSiteRequirements = apps.get_model("facilities", "AssetSiteRequirements")

    for profile in AssetSiteRequirements.objects.select_related("asset").all():
        asset = profile.asset
        asset.breaker_id = profile.breaker_id
        asset.disconnect_id = profile.disconnect_id
        asset.needs_compressed_air = profile.needs_compressed_air
        asset.needs_ventilation = profile.needs_ventilation
        asset.save(
            update_fields=[
                "breaker",
                "disconnect",
                "needs_compressed_air",
                "needs_ventilation",
            ]
        )

    AssetSiteRequirements.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("facilities", "0001_initial"),
        # Depend on the inventory + electrical_circuits leaves where the Asset
        # fields still exist (before inventory.0083 removes them).
        ("inventory", "0082_inventoryitem_is_retired_inventoryitem_retired_at"),
        ("electrical_circuits", "0013_powerbreaker_critical_category_and_more"),
    ]

    operations = [
        migrations.RunPython(
            copy_requirements_forward,
            restore_requirements_backward,
        ),
    ]
