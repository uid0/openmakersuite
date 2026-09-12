from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("inventory", "0112_alter_itemsupplier_unit_cost")]

    operations = [
        migrations.AddField(
            model_name="itemsupplier",
            name="average_lead_time_provenance",
            field=models.CharField(
                choices=[
                    ("default", "Planning default"),
                    ("recorded", "Recorded"),
                    ("unknown", "Unknown"),
                ],
                default="unknown",
                max_length=10,
            ),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="itemsupplier",
            name="average_lead_time_provenance",
            field=models.CharField(
                choices=[
                    ("default", "Planning default"),
                    ("recorded", "Recorded"),
                    ("unknown", "Unknown"),
                ],
                default="default",
                max_length=10,
            ),
        ),
    ]
