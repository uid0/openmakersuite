"""Name the unit ``average_lead_time`` is recorded in, where the operator reads it.

``help_text`` only — no column, index or data is touched, and nothing already
stored changes. The number was always CALENDAR days
(``inventory.tasks.update_average_lead_times`` writes it from elapsed days, and
``LeadTimeLog`` grades a delivery against it the same way), but the label said
only "days", and a reader who assumed working days wrote the published delivery
date in business days three times over. See
:mod:`inventory.services.lead_times`.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0112_alter_itemsupplier_unit_cost"),
    ]

    operations = [
        migrations.AlterField(
            model_name="itemsupplier",
            name="average_lead_time",
            field=models.PositiveIntegerField(
                default=7, help_text="Average lead time in CALENDAR days from this supplier"
            ),
        ),
    ]
