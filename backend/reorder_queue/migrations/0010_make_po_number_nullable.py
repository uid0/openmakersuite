# Generated manually to fix NOT NULL constraint issue

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reorder_queue", "0009_add_asset_to_purchaseorderitem"),
    ]

    operations = [
        migrations.AlterField(
            model_name="purchaseorder",
            name="po_number",
            field=models.CharField(
                blank=True,
                help_text="Purchase Order Number",
                max_length=50,
                null=True,
                unique=True,
            ),
        ),
    ]

