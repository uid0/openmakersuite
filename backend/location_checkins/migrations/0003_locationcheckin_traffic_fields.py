from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):

    dependencies = [
        ("location_checkins", "0002_remove_securityreport_location_ch_severit_197128_idx_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="locationcheckin",
            name="checked_in_at",
            field=models.DateTimeField(
                default=timezone.now, help_text="When the check-in occurred"
            ),
        ),
        migrations.AddField(
            model_name="locationcheckin",
            name="device_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text=(
                    "Identifier of the IoT device that recorded this check-in "
                    "(blank for manual/QR)"
                ),
                max_length=100,
            ),
        ),
        migrations.AddField(
            model_name="locationcheckin",
            name="source",
            field=models.CharField(
                choices=[
                    ("manual", "Manual"),
                    ("qr", "QR Code"),
                    ("device", "IoT Device"),
                ],
                default="qr",
                help_text="How this check-in was recorded",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="locationcheckin",
            name="event_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text=(
                    "Optional client-supplied event id used for idempotent "
                    "webhook ingest"
                ),
                max_length=100,
            ),
        ),
        migrations.AddIndex(
            model_name="locationcheckin",
            index=models.Index(
                fields=["source", "-checked_in_at"],
                name="location_ch_source_ci_idx",
            ),
        ),
    ]
