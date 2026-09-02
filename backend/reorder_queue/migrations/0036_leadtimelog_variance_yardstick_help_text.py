from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reorder_queue", "0035_backfill_lead_time_calendar_days"),
    ]

    operations = [
        migrations.AlterField(
            model_name="leadtimelog",
            name="variance_days",
            field=models.IntegerField(
                help_text=(
                    "Actual minus estimated lead time, in calendar days (positive = "
                    "later than quoted). Measured against the supplier link's standing "
                    "quoted lead time, NOT against expected_delivery_date, which is the "
                    "order's separately confirmed date and a different fact."
                )
            ),
        ),
    ]
