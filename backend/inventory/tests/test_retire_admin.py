"""Admin bulk-action tests for InventoryItem retire/unretire (op-jv7r).

Exercises the ``retire_selected`` / ``unretire_selected`` changelist actions
directly, verifying they flip ``is_retired`` and manage the ``retired_at``
audit stamp without disturbing items already in the target state.
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.utils import timezone

from inventory.admin import InventoryItemAdmin
from inventory.models import InventoryItem
from inventory.tests.factories import InventoryItemFactory

User = get_user_model()


class InventoryItemRetireAdminActionTest(TestCase):
    """The retire_selected / unretire_selected bulk actions behave correctly."""

    def setUp(self):
        self.admin = InventoryItemAdmin(InventoryItem, AdminSite())
        self.request = RequestFactory().post("/admin/inventory/inventoryitem/")
        self.request.user = User.objects.create_superuser(
            username="retire-admin", email="retire-admin@test.com", password="pw"
        )

    def test_retire_selected_flags_and_stamps_only_new_items(self):
        active = InventoryItemFactory(is_retired=False)
        original_stamp = timezone.now() - timedelta(days=5)
        already = InventoryItemFactory(is_retired=True, retired_at=original_stamp)

        queryset = InventoryItem.objects.filter(pk__in=[active.pk, already.pk])
        with patch.object(self.admin, "message_user"):
            self.admin.retire_selected(self.request, queryset)

        active.refresh_from_db()
        already.refresh_from_db()
        self.assertTrue(active.is_retired)
        self.assertIsNotNone(active.retired_at)
        # An already-retired item keeps its original stamp (not re-stamped).
        self.assertEqual(already.retired_at, original_stamp)

    def test_unretire_selected_clears_flag_and_stamp(self):
        retired = InventoryItemFactory(is_retired=True, retired_at=timezone.now())

        queryset = InventoryItem.objects.filter(pk=retired.pk)
        with patch.object(self.admin, "message_user"):
            self.admin.unretire_selected(self.request, queryset)

        retired.refresh_from_db()
        self.assertFalse(retired.is_retired)
        self.assertIsNone(retired.retired_at)
