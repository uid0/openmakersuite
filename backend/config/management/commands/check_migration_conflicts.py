"""``manage.py check_migration_conflicts`` — fail on a multi-leaf migration graph.

Two PRs that each branch a new migration off the same parent (e.g. two
``0079`` migrations off ``0078_supplier_ordering_adapter``) produce *two leaf
nodes* in that app's migration graph once both land on ``main``. Django's
``migrate`` refuses to run against such a graph — it aborts at startup with::

    Conflicting migrations detected; multiple leaf nodes in the migration
    graph: (0079_alter_workordersubmission_source,
    0079_assetmeter_assetmeterreading_and_more in inventory).

which turns every deploy into an outage until a merge migration is committed
(the 2026-07-08 incident + PR #864). This command surfaces that same conflict
at PR time so CI fails instead of the deploy.

It inspects the migration graph *on disk only* (``MigrationLoader(None)``), so
it needs no database connection and keys off exactly what
``makemigrations --merge`` would. CI runs it as its own step; the companion
test ``config/tests/test_migration_conflicts.py`` keeps the repo's own graph
single-leaf.

Note for reviewers: the existing ``makemigrations --check --dry-run`` drift
step *does* also abort on a multi-leaf graph in the current Django, but only
as an incidental side effect of graph loading — its stated job is
missing-migration drift. This command is the explicit, purpose-built conflict
gate, so a distinct CI failure tells a developer to run ``--merge`` rather
than ``makemigrations``.
"""

from __future__ import annotations

import sys

from django.core.management.base import BaseCommand
from django.db.migrations.loader import MigrationLoader


class Command(BaseCommand):
    help = "Fail if any app's migration graph has multiple leaf nodes (a conflict)."

    def handle(self, *args, **options):
        # connection=None builds the graph from the migration files on disk
        # without querying any database for applied migrations — the same
        # loader makemigrations uses to detect merge conflicts. This keeps the
        # check fast and usable in any CI job, DB or not.
        loader = MigrationLoader(None, ignore_no_migrations=True)
        conflicts = loader.detect_conflicts()

        if not conflicts:
            self.stdout.write(
                self.style.SUCCESS("OK — every app's migration graph has a single leaf node.")
            )
            return

        self.stderr.write(
            self.style.ERROR(
                "Conflicting migrations detected; multiple leaf nodes in the migration graph:"
            )
        )
        for app_label, leaves in sorted(conflicts.items()):
            self.stderr.write(f"  {app_label}: {', '.join(sorted(leaves))}")
        self.stderr.write(
            "\nTwo migrations branch off the same parent. Rebase onto the latest "
            "main and run `python manage.py makemigrations --merge` to add a merge "
            "migration, then commit it."
        )
        sys.exit(1)
