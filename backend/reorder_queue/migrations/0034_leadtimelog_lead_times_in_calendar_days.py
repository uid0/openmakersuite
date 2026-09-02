from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reorder_queue", "0033_alter_purchaseorderauditevent_action"),
    ]

    operations = [
        migrations.AlterField(
            model_name="leadtimelog",
            name="actual_lead_time_days",
            field=models.PositiveIntegerField(help_text="Actual lead time in calendar days"),
        ),
        migrations.AlterField(
            model_name="leadtimelog",
            name="estimated_lead_time_days",
            field=models.PositiveIntegerField(help_text="Estimated lead time in calendar days"),
        ),
    ]
