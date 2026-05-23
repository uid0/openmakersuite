# Generated manually

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reorder_queue", "0008_add_expected_shipment_date_to_purchaseorderitem"),
        ("inventory", "0033_location_qr_code"),
    ]

    operations = [
        migrations.AddField(
            model_name="purchaseorderitem",
            name="asset",
            field=models.ForeignKey(
                blank=True,
                help_text="Asset being purchased (for asset purchases)",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="purchase_order_items",
                to="inventory.asset",
            ),
        ),
        migrations.AlterField(
            model_name="purchaseorderitem",
            name="item_supplier",
            field=models.ForeignKey(
                blank=True,
                help_text="Specific supplier relationship for inventory items",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                to="inventory.itemsupplier",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="purchaseorderitem",
            unique_together={
                ("purchase_order", "item_supplier"),
                ("purchase_order", "asset"),
            },
        ),
        migrations.AddIndex(
            model_name="purchaseorderitem",
            index=models.Index(
                fields=["purchase_order", "asset"],
                name="reorder_que_purchas_asset_i_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="purchaseorderitem",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("item_supplier__isnull", False), ("asset__isnull", True))
                    | models.Q(
                        ("item_supplier__isnull", True), ("asset__isnull", False)
                    )
                ),
                name="purchase_order_item_must_have_item_or_asset",
            ),
        ),
    ]
