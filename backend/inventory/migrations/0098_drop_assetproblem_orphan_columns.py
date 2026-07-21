"""Drop three orphan columns on inventory_assetproblem (Sentry BACKEND-12).

Production's ``inventory_assetproblem`` table carried three columns that exist
in neither the ``AssetProblem`` model nor any migration — an out-of-band schema
change (an abandoned "logistics escalation" experiment):

    * ``is_urgent``               boolean NOT NULL, no default
    * ``escalated_to_logistics``  boolean, default false
    * ``mitigation_notes``        text, default ''

Because ``is_urgent`` is NOT NULL with no default and the code never sets it,
every ``AssetProblem`` INSERT (``AssetViewSet.report_problem``) failed with a
NotNullViolation. This reconciles the schema to the code by dropping all three.

Guarded with ``IF EXISTS`` / ``IF NOT EXISTS`` so it is a no-op on databases
that never had the columns (fresh dev/CI). RunSQL only: the model never declared
these fields, so Django's migration state is already correct and unaffected.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0097_work_order_elapsed_timer"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                "ALTER TABLE inventory_assetproblem DROP COLUMN IF EXISTS is_urgent;",
                "ALTER TABLE inventory_assetproblem DROP COLUMN IF EXISTS escalated_to_logistics;",
                "ALTER TABLE inventory_assetproblem DROP COLUMN IF EXISTS mitigation_notes;",
            ],
            # Reverse re-adds the columns defensively — is_urgent comes back with
            # a default so a rollback cannot reintroduce the BACKEND-12 crash.
            reverse_sql=[
                "ALTER TABLE inventory_assetproblem ADD COLUMN IF NOT EXISTS is_urgent boolean NOT NULL DEFAULT false;",
                "ALTER TABLE inventory_assetproblem ADD COLUMN IF NOT EXISTS escalated_to_logistics boolean DEFAULT false;",
                "ALTER TABLE inventory_assetproblem ADD COLUMN IF NOT EXISTS mitigation_notes text DEFAULT '';",
            ],
        ),
    ]
