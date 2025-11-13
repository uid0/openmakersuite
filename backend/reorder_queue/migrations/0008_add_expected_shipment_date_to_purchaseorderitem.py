# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reorder_queue", "0007_alter_webhook_event_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="purchaseorderitem",
            name="expected_shipment_date",
            field=models.DateField(
                blank=True,
                help_text="Expected shipment date for this line item (useful for items with longer lead times)",
                null=True,
            ),
        ),
    ]

