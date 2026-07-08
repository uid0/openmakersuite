"""Regression tests for the admin "Duplicate asset" view (op-9oem).

`AssetAdmin.duplicate_asset_view` promises in its form help text that "All other
information will be copied from the original asset". Before op-9oem it copied the
Asset's own fields but silently dropped the ``AssetPart`` through-model rows, so
clones had no parts. These tests pin the fixed behavior: the clone receives
matching AssetPart configuration rows, and per-instance replacement history
(``last_replaced_at`` / ``replacement_serial_number``) resets on the clone.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from inventory.models import Asset
from inventory.tests.factories import (
    AssetFactory,
    AssetPartFactory,
    InventoryItemFactory,
)

User = get_user_model()


class DuplicateAssetCopiesPartsTest(TestCase):
    """`duplicate_asset_view` copies AssetPart config and resets replacement history."""

    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="asset-dup-admin",
            email="asset-dup-admin@test.com",
            password="pw",
        )
        self.client.force_login(self.admin_user)

        self.original = AssetFactory(serial_number="ORIG-SERIAL-001")

        # A required part carrying replacement history that must NOT be cloned.
        self.required_part = AssetPartFactory(
            asset=self.original,
            part=InventoryItemFactory(name="HEPA Filter"),
            quantity_needed=3,
            is_required=True,
            maintenance_interval_days=90,
            notes="Replace filter quarterly",
            last_replaced_at=timezone.now(),
            replacement_serial_number="REPL-SN-999",
        )
        # An optional part with no replacement history recorded.
        self.optional_part = AssetPartFactory(
            asset=self.original,
            part=InventoryItemFactory(name="Spare Blade"),
            quantity_needed=2,
            is_required=False,
            maintenance_interval_days=180,
            notes="Spare blade for the fence",
        )

    def _duplicate(self, serial_number):
        url = reverse("admin:inventory_asset_duplicate", args=[self.original.pk])
        return self.client.post(url, {"serial_number": serial_number})

    def test_clone_receives_matching_asset_part_config(self):
        """Clone gets an AssetPart per source part with identical config fields."""
        response = self._duplicate("CLONE-SERIAL-001")
        self.assertEqual(response.status_code, 302)

        clone = Asset.objects.get(serial_number="CLONE-SERIAL-001")
        self.assertNotEqual(clone.pk, self.original.pk)

        clone_parts = {p.part_id: p for p in clone.asset_parts.all()}
        self.assertEqual(len(clone_parts), 2)

        for source in (self.required_part, self.optional_part):
            cloned = clone_parts[source.part_id]
            self.assertEqual(cloned.quantity_needed, source.quantity_needed)
            self.assertEqual(cloned.is_required, source.is_required)
            self.assertEqual(
                cloned.maintenance_interval_days,
                source.maintenance_interval_days,
            )
            self.assertEqual(cloned.notes, source.notes)

    def test_clone_resets_replacement_history(self):
        """Cloned parts start fresh: no last_replaced_at, blank replacement serial."""
        self._duplicate("CLONE-SERIAL-002")
        clone = Asset.objects.get(serial_number="CLONE-SERIAL-002")

        cloned_parts = list(clone.asset_parts.all())
        self.assertEqual(len(cloned_parts), 2)
        for cloned in cloned_parts:
            self.assertIsNone(cloned.last_replaced_at)
            self.assertEqual(cloned.replacement_serial_number, "")

    def test_original_asset_parts_are_unchanged(self):
        """Cloning does not disturb the source asset's parts or their history."""
        self._duplicate("CLONE-SERIAL-003")

        self.required_part.refresh_from_db()
        self.assertIsNotNone(self.required_part.last_replaced_at)
        self.assertEqual(self.required_part.replacement_serial_number, "REPL-SN-999")
        self.assertEqual(self.original.asset_parts.count(), 2)
