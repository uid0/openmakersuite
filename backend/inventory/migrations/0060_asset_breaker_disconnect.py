"""
Add direct ``breaker`` and ``disconnect`` FKs to Asset.

Replaces the previous ``Asset → PowerPort → Cable → PowerOutlet`` cordset
chain and the ``HardwiredConnection`` join row. The disconnect FK preserves
the LOTO-relevant data that operators wanted kept.
"""

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0059_add_maintenance_record"),
        ("electrical_circuits", "0011_drop_cable_powerport_hardwired"),
    ]

    operations = [
        migrations.AddField(
            model_name="asset",
            name="breaker",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Breaker that feeds this asset (replaces the previous cable-and-" "port chain)."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="assets",
                to="electrical_circuits.powerbreaker",
            ),
        ),
        migrations.AddField(
            model_name="asset",
            name="disconnect",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Disconnect switch used to isolate this asset for lock-out / tag-"
                    "out. Optional — only the disconnect-relevant LOTO data is kept "
                    "on the asset; the rest of the cable/cordset modelling has been "
                    "removed."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="assets",
                to="electrical_circuits.disconnect",
            ),
        ),
    ]
