# Generated manually to fix NOT NULL constraint issue

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reorder_queue", "0010_alter_purchaseorderitem_options_and_more"),
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

