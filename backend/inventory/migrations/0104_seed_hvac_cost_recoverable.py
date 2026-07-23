"""Seed ``Asset.is_cost_recoverable`` for the HVAC fleet (op-srrv, B5).

Per the requirement that started this bead: "certain assets — the HVAC units —
are recoverable". Every other asset stays at the ``False`` default, so the
recoverable Actual column keeps today's numbers until somebody opts an asset in.

There is no canonical HVAC category shipped with the app (categories are
operator-created), so this matches on the category *name* rather than a pinned
PK: any category whose name contains "HVAC", case-insensitively. On a database
without one this is a no-op. Operators who name the category something else use
the ``AssetAdmin`` "Mark selected assets as landlord cost-recoverable" action.

Irreversible in the sense that matters: the reverse is a deliberate no-op
because we cannot tell an asset this migration flagged from one an operator
flagged by hand, and clearing the latter would lose real data.
"""

from django.db import migrations

#: Case-insensitive substring identifying the HVAC category by name.
HVAC_CATEGORY_MATCH = "hvac"


def seed_hvac_cost_recoverable(apps, schema_editor):
    Asset = apps.get_model("inventory", "Asset")
    Category = apps.get_model("inventory", "Category")

    category_ids = list(
        Category.objects.filter(name__icontains=HVAC_CATEGORY_MATCH).values_list("id", flat=True)
    )
    if not category_ids:
        return
    Asset.objects.filter(category_id__in=category_ids, is_cost_recoverable=False).update(
        is_cost_recoverable=True
    )


def unseed_hvac_cost_recoverable(apps, schema_editor):
    """Deliberate no-op — see the module docstring."""


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0103_asset_is_cost_recoverable"),
    ]

    operations = [
        migrations.RunPython(seed_hvac_cost_recoverable, unseed_hvac_cost_recoverable),
    ]
