"""Give each supplier link an optimistic-concurrency version.

Adds ``ItemSupplier.version``. Every EXISTING row starts at ``1``: no client
holds a token yet, so any starting number is equally truthful, and a write that
carries no token is not checked. PostgreSQL adds a column with a constant
default without rewriting the table. See :mod:`inventory.services.link_version`.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0114_itemsupplier_average_lead_time_source"),
    ]

    operations = [
        migrations.AddField(
            model_name="itemsupplier",
            name="version",
            field=models.PositiveIntegerField(
                default=1,
                editable=False,
                help_text="Moves on by one with every write to this link. A save that states the version it loaded is refused once the link has moved on.",
            ),
        ),
    ]
