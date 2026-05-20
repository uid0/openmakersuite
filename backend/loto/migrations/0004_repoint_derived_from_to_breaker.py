"""
Re-target ``AssetEnergySource.derived_from`` from the Cable model (which is
being removed in :mod:`electrical_circuits.migrations.0011`) to PowerBreaker.

Derived rows are auto-populated by :func:`loto.services.power_derivation.
derive_for_asset` from the asset's direct ``breaker`` FK. We drop the old FK
and re-add a fresh one pointing at PowerBreaker; existing derived rows lose
their FK pointer but the row content (isolation_point, magnitude, required
devices) survives — the re-derivation pass run as part of this rollout
re-attaches the breaker reference.
"""

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("loto", "0003_backfill_derived_energy_sources"),
        ("electrical_circuits", "0010_disconnect_hardwiredconnection"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="assetenergysource",
            name="derived_from",
        ),
        migrations.AddField(
            model_name="assetenergysource",
            name="derived_from",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Power breaker this row was auto-derived from. NULL for manual "
                    "entries (hydraulic, pneumatic, etc.) and for derived rows whose "
                    "source breaker was hard-deleted."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="derived_energy_sources",
                to="electrical_circuits.powerbreaker",
            ),
        ),
        migrations.AlterField(
            model_name="assetenergysource",
            name="is_stale",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "True when the breaker that derived this row was removed or the "
                    "asset is no longer assigned to it. Kept (rather than deleted) so "
                    "the LOTO audit trail survives equipment moves."
                ),
            ),
        ),
    ]
