import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0045_add_work_order_submission"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="maintenanceitem",
            name="instructions",
        ),
        migrations.AddField(
            model_name="maintenancematerial",
            name="inventory_item",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "The tracked inventory item used for this material. "
                    "Links maintenance to stock so we can verify supplies are on hand."
                ),
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="maintenance_material_usages",
                to="inventory.inventoryitem",
            ),
        ),
        migrations.AlterField(
            model_name="maintenancematerial",
            name="name",
            field=models.CharField(
                blank=True,
                help_text=(
                    "Display name for the material. Falls back to inventory_item.name "
                    "when blank. Kept for legacy rows; new rows should use inventory_item."
                ),
                max_length=200,
            ),
        ),
    ]
