"""
Tests for SIGAdmin model and utilities.
"""

from django.contrib.auth.models import Group

import pytest

from membership.models import SIGAdmin
from membership.tests.factories import UserFactory
from membership.utils import (
    can_create_reorder_request,
    can_manage_sig_asset,
    can_manage_sig_inventory,
    get_user_managed_sigs,
    is_logistics_member,
    is_sig_admin,
)


@pytest.mark.unit
class TestSIGAdminModel:
    """Tests for SIGAdmin model."""

    def test_sigadmin_creation(self):
        """Test creating a SIGAdmin instance."""
        user = UserFactory()
        group = Group.objects.create(name="Test SIG")
        sig_admin = SIGAdmin.objects.create(user=user, group=group)

        assert sig_admin.user == user
        assert sig_admin.group == group
        assert sig_admin.is_active is True

    def test_sigadmin_unique_together(self):
        """Test that user-group combination must be unique."""
        from django.db import IntegrityError

        user = UserFactory()
        group = Group.objects.create(name="Test SIG")
        SIGAdmin.objects.create(user=user, group=group)

        with pytest.raises(IntegrityError):
            SIGAdmin.objects.create(user=user, group=group)

    def test_sigadmin_is_sig_admin_classmethod(self):
        """Test is_sig_admin classmethod."""
        user = UserFactory()
        group = Group.objects.create(name="Test SIG")
        SIGAdmin.objects.create(user=user, group=group, is_active=True)

        assert SIGAdmin.is_sig_admin(user, group) is True
        assert SIGAdmin.is_sig_admin(user, Group.objects.create(name="Other SIG")) is False

    def test_sigadmin_is_sig_admin_inactive(self):
        """Test is_sig_admin returns False for inactive admins."""
        user = UserFactory()
        group = Group.objects.create(name="Test SIG")
        SIGAdmin.objects.create(user=user, group=group, is_active=False)

        assert SIGAdmin.is_sig_admin(user, group) is False

    def test_sigadmin_get_user_sigs(self):
        """Test get_user_sigs classmethod."""
        user = UserFactory()
        group1 = Group.objects.create(name="SIG 1")
        group2 = Group.objects.create(name="SIG 2")
        group3 = Group.objects.create(name="SIG 3")

        SIGAdmin.objects.create(user=user, group=group1, is_active=True)
        SIGAdmin.objects.create(user=user, group=group2, is_active=True)
        SIGAdmin.objects.create(user=user, group=group3, is_active=False)  # Inactive

        user_sigs = SIGAdmin.get_user_sigs(user)
        assert user_sigs.count() == 2
        assert group1 in user_sigs
        assert group2 in user_sigs
        assert group3 not in user_sigs

    def test_sigadmin_get_sig_admins(self):
        """Test get_sig_admins classmethod."""
        group = Group.objects.create(name="Test SIG")
        admin1 = UserFactory()
        admin2 = UserFactory()
        non_admin = UserFactory()

        SIGAdmin.objects.create(user=admin1, group=group, is_active=True)
        SIGAdmin.objects.create(user=admin2, group=group, is_active=True)
        SIGAdmin.objects.create(user=non_admin, group=group, is_active=False)  # Inactive

        sig_admins = SIGAdmin.get_sig_admins(group)
        assert sig_admins.count() == 2
        assert admin1 in sig_admins
        assert admin2 in sig_admins
        assert non_admin not in sig_admins


@pytest.mark.unit
class TestSIGPermissionUtils:
    """Tests for SIG permission utility functions."""

    def test_is_sig_admin(self):
        """Test is_sig_admin utility function."""
        user = UserFactory()
        group = Group.objects.create(name="Test SIG")
        SIGAdmin.objects.create(user=user, group=group, is_active=True)

        assert is_sig_admin(user, group) is True
        assert is_sig_admin(user, Group.objects.create(name="Other SIG")) is False

    def test_is_logistics_member(self):
        """Test is_logistics_member utility function."""
        from django.contrib.auth import get_user_model
        from django.db import connection

        User = get_user_model()
        user = UserFactory()
        logistics_group = Group.objects.create(name="Logistics")
        
        # Check if auth_user_groups table exists, if not, create it
        with connection.cursor() as cursor:
            if connection.vendor == "sqlite":
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='auth_user_groups';"
                )
                table_exists = cursor.fetchone() is not None
            else:
                # For other databases, assume table exists
                table_exists = True
            
            if not table_exists:
                # Create the table
                cursor.execute(
                    """
                    CREATE TABLE auth_user_groups (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        group_id INTEGER NOT NULL,
                        UNIQUE(user_id, group_id),
                        FOREIGN KEY (user_id) REFERENCES auth_user(id),
                        FOREIGN KEY (group_id) REFERENCES auth_group(id)
                    );
                    """
                )
        
        # Use through model directly to ensure migrations are applied
        User.groups.through.objects.create(user=user, group=logistics_group)

        assert is_logistics_member(user) is True

        non_logistics_user = UserFactory()
        assert is_logistics_member(non_logistics_user) is False

    def test_get_user_managed_sigs(self):
        """Test get_user_managed_sigs utility function."""
        user = UserFactory()
        group1 = Group.objects.create(name="SIG 1")
        group2 = Group.objects.create(name="SIG 2")

        SIGAdmin.objects.create(user=user, group=group1, is_active=True)
        SIGAdmin.objects.create(user=user, group=group2, is_active=True)

        user_sigs = get_user_managed_sigs(user)
        assert user_sigs.count() == 2
        assert group1 in user_sigs
        assert group2 in user_sigs

    def test_can_manage_sig_asset(self):
        """Test can_manage_sig_asset utility function."""
        from django.contrib.auth import get_user_model

        from inventory.tests.factories import AssetFactory

        User = get_user_model()
        # Create test data
        sig_admin = UserFactory()
        regular_user = UserFactory()
        staff_user = UserFactory(is_staff=True)
        logistics_user = UserFactory()
        logistics_group = Group.objects.create(name="Logistics")
        # Use through model directly to ensure migrations are applied
        User.groups.through.objects.create(user=logistics_user, group=logistics_group)

        sig_group = Group.objects.create(name="Test SIG")
        SIGAdmin.objects.create(user=sig_admin, group=sig_group, is_active=True)

        # Asset owned by SIG
        asset = AssetFactory(owning_group=sig_group)

        # SIG admin can manage
        assert can_manage_sig_asset(sig_admin, asset) is True

        # Regular user cannot manage
        assert can_manage_sig_asset(regular_user, asset) is False

        # Staff can manage
        assert can_manage_sig_asset(staff_user, asset) is True

        # Logistics can manage
        assert can_manage_sig_asset(logistics_user, asset) is True

        # Space-owned asset (no owning group)
        space_asset = AssetFactory(owning_group=None)
        assert can_manage_sig_asset(sig_admin, space_asset) is False
        assert can_manage_sig_asset(staff_user, space_asset) is True
        assert can_manage_sig_asset(logistics_user, space_asset) is True

    def test_can_manage_sig_inventory(self):
        """Test can_manage_sig_inventory utility function."""
        from django.contrib.auth import get_user_model

        from inventory.tests.factories import InventoryItemFactory

        User = get_user_model()
        # Create test data
        sig_admin = UserFactory()
        regular_user = UserFactory()
        staff_user = UserFactory(is_staff=True)
        logistics_user = UserFactory()
        logistics_group = Group.objects.create(name="Logistics")
        # Use through model directly to ensure migrations are applied
        User.groups.through.objects.create(user=logistics_user, group=logistics_group)

        sig_group = Group.objects.create(name="Test SIG")
        SIGAdmin.objects.create(user=sig_admin, group=sig_group, is_active=True)

        # Inventory item owned by SIG
        item = InventoryItemFactory(owning_group=sig_group)

        # SIG admin can manage
        assert can_manage_sig_inventory(sig_admin, item) is True

        # Regular user cannot manage
        assert can_manage_sig_inventory(regular_user, item) is False

        # Staff can manage
        assert can_manage_sig_inventory(staff_user, item) is True

        # Logistics can manage
        assert can_manage_sig_inventory(logistics_user, item) is True

        # Space-owned item (no owning group)
        space_item = InventoryItemFactory(owning_group=None)
        assert can_manage_sig_inventory(sig_admin, space_item) is False
        assert can_manage_sig_inventory(staff_user, space_item) is True
        assert can_manage_sig_inventory(logistics_user, space_item) is True

    def test_can_create_reorder_request(self):
        """Test can_create_reorder_request utility function."""
        from django.contrib.auth import get_user_model

        from inventory.tests.factories import InventoryItemFactory

        User = get_user_model()
        # Create test data
        sig_admin = UserFactory()
        regular_user = UserFactory()
        staff_user = UserFactory(is_staff=True)
        logistics_user = UserFactory()
        logistics_group = Group.objects.create(name="Logistics")
        # Use through model directly to ensure migrations are applied
        User.groups.through.objects.create(user=logistics_user, group=logistics_group)

        sig_group = Group.objects.create(name="Test SIG")
        SIGAdmin.objects.create(user=sig_admin, group=sig_group, is_active=True)

        # SIG-owned item, requestable
        item = InventoryItemFactory(owning_group=sig_group, is_requestable=True)

        # SIG admin can create request
        assert can_create_reorder_request(sig_admin, item) is True

        # Regular user cannot create request for SIG-owned item
        assert can_create_reorder_request(regular_user, item) is False

        # Staff can always create
        assert can_create_reorder_request(staff_user, item) is True

        # Logistics can always create
        assert can_create_reorder_request(logistics_user, item) is True

        # Space-owned item, requestable - any authenticated user can request
        space_item = InventoryItemFactory(owning_group=None, is_requestable=True)
        assert can_create_reorder_request(regular_user, space_item) is True

        # Non-requestable item
        non_requestable = InventoryItemFactory(owning_group=sig_group, is_requestable=False)
        assert can_create_reorder_request(sig_admin, non_requestable) is False
        assert can_create_reorder_request(staff_user, non_requestable) is True
        assert can_create_reorder_request(logistics_user, non_requestable) is True
