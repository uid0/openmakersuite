"""Record where each supplier link's lead time came from.

Adds ``ItemSupplier.average_lead_time_source``. Every EXISTING row is backfilled
``unknown``: whether a stored 7 was typed or taken from the default was never
captured, so no known provenance may be claimed for it. New and changed values
are labelled by ``ItemSupplier.save()`` — see
:mod:`inventory.services.lead_time_source`.

The ``average_lead_time`` change is Python-side only — the planning default now
carries its own "not supplied" mark, and the field keeps that mark through
``full_clean`` — and ``sqlmigrate`` shows it as a no-op. No lead time VALUE is touched.
"""

import inventory.services.lead_time_source
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0113_alter_itemsupplier_average_lead_time"),
    ]

    operations = [
        migrations.AddField(
            model_name="itemsupplier",
            name="average_lead_time_source",
            field=models.CharField(
                choices=[
                    ("unknown", "Unknown (stored before its source was kept)"),
                    ("default", "Planning default (nobody recorded one)"),
                    ("recorded", "Recorded"),
                    ("measured", "Measured from deliveries"),
                ],
                default="unknown",
                editable=False,
                help_text="Where average_lead_time came from. Decided by save() from how the value was obtained; never set directly.",
                max_length=8,
            ),
        ),
        migrations.AlterField(
            model_name="itemsupplier",
            name="average_lead_time",
            field=inventory.services.lead_time_source.LeadTimeDaysField(
                default=inventory.services.lead_time_source.planning_default,
                help_text="Average lead time in CALENDAR days from this supplier",
            ),
        ),
    ]
