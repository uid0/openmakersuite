"""Tests for the 0011 inventory_membership repair migration.

The repair migration must be a safe, idempotent no-op on databases that already
have the tables (CI/dev/fresh), and must never disturb existing rows. The
"recreate when missing" path is exercised against the affected production
database; here we assert the critical safety property: running it where the
tables already exist is harmless and repeatable.
"""

import importlib

from django.db import connection
from django.test import TestCase

from membership.models import Membership

_repair = importlib.import_module("membership.migrations.0011_ensure_inventory_membership_table")


class EnsureInventoryMembershipTablesTest(TestCase):
    def _run_repair(self):
        with connection.schema_editor(atomic=False) as schema_editor:
            _repair.ensure_inventory_membership_tables(None, schema_editor)

    def test_idempotent_and_nondestructive_when_tables_exist(self):
        # The migrated test DB already has inventory_membership (from 0001).
        membership = Membership.objects.create()

        # Running the repair once, then again, must not raise and must leave
        # the existing table and its data untouched.
        self._run_repair()
        self._run_repair()

        self.assertTrue(Membership.objects.filter(pk=membership.pk).exists())
        self.assertIn("inventory_membership", connection.introspection.table_names())
        self.assertIn("inventory_membership_users", connection.introspection.table_names())
