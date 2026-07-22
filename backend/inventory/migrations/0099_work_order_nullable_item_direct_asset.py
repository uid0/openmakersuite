import django.db.models.deletion
from django.db import migrations, models


def backfill_work_order_asset(apps, schema_editor):
    """Populate the new direct asset FK from each WO's PM template.

    Every pre-existing work order came from a ``MaintenanceItem``, so the
    template's asset *is* the work order's asset. Done in one UPDATE...FROM-ish
    subquery pass rather than row-by-row: this runs against production tables.
    """
    WorkOrder = apps.get_model("inventory", "WorkOrder")
    MaintenanceItem = apps.get_model("inventory", "MaintenanceItem")

    WorkOrder.objects.filter(asset__isnull=True, maintenance_item__isnull=False).update(
        asset_id=models.Subquery(
            MaintenanceItem.objects.filter(pk=models.OuterRef("maintenance_item_id")).values(
                "asset_id"
            )[:1]
        )
    )


def unbackfill_work_order_asset(apps, schema_editor):
    """No-op reverse: the column is dropped by the AddField reversal anyway."""


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0098_drop_assetproblem_orphan_columns"),
    ]

    operations = [
        migrations.AddField(
            model_name="workorder",
            name="asset",
            field=models.ForeignKey(
                blank=True,
                help_text="The asset this work order is for",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="work_orders",
                to="inventory.asset",
            ),
        ),
        migrations.AlterField(
            model_name="workorder",
            name="maintenance_item",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "The PM template this work order is for. Null for corrective work "
                    "orders, which carry no template — read ``asset`` for the machine."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="work_orders",
                to="inventory.maintenanceitem",
            ),
        ),
        migrations.RunPython(backfill_work_order_asset, unbackfill_work_order_asset),
    ]
