"""Tests for the ownership service surface introduced in #881.

These cover the *new* helpers added when the ownership/SIG-permission auth was
consolidated into :mod:`membership.services`:

* :func:`is_system_admin` / :func:`is_owning_group_admin` — back the
  ``OwnableModel`` mixin's ``is_user_*`` delegators.
* :func:`can_assign_to_owning_group` — the assign/reassign-to-SIG write gate.
* :func:`scope_queryset_by_ownership` — the parameterized visibility helper,
  under both :class:`OwnershipVisibility` policies and via a related field path.

The byte-for-byte truth-table for the pre-existing gates
(``can_manage_sig_*`` / ``can_create_reorder_request`` / ...) lives in
``test_sigadmin.py`` and is intentionally left untouched.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group

import pytest

from inventory.models import Asset, InventoryItem
from inventory.models.ownership import OwnableModel
from inventory.tests.factories import AssetFactory, InventoryItemFactory
from membership.models import SIGAdmin
from membership.services import (
    OwnershipVisibility,
    can_assign_to_owning_group,
    is_owning_group_admin,
    is_system_admin,
    scope_queryset_by_ownership,
)
from membership.tests.factories import UserFactory
from reorder_queue.tests.factories import ReorderRequestFactory

pytestmark = pytest.mark.django_db


def _add_to_group(user, group):
    """Attach ``user`` to ``group`` via the through model (mirrors the pattern
    used across the existing membership tests so migrations are honoured)."""
    get_user_model().groups.through.objects.create(user=user, group=group)


@pytest.fixture
def logistics_group():
    return Group.objects.create(name="Logistics")


@pytest.mark.unit
class TestOwnershipPrimitives:
    def test_is_system_admin(self):
        assert is_system_admin(UserFactory(is_staff=True)) is True
        assert is_system_admin(UserFactory(is_superuser=True)) is True
        assert is_system_admin(UserFactory()) is False
        assert is_system_admin(AnonymousUser()) is False

    def test_is_owning_group_admin(self):
        sig = Group.objects.create(name="Woodshop")
        other = Group.objects.create(name="Metalshop")
        admin = UserFactory()
        SIGAdmin.objects.create(user=admin, group=sig, is_active=True)

        assert is_owning_group_admin(admin, sig) is True
        # admin of a different SIG is not an admin of this group
        assert is_owning_group_admin(admin, other) is False
        # no owning group -> False (space-owned)
        assert is_owning_group_admin(admin, None) is False
        # unauthenticated -> False
        assert is_owning_group_admin(AnonymousUser(), sig) is False


@pytest.mark.unit
class TestCanAssignToOwningGroup:
    def test_matrix(self, logistics_group):
        sig = Group.objects.create(name="Woodshop")
        other = Group.objects.create(name="Metalshop")

        staff = UserFactory(is_staff=True)
        superu = UserFactory(is_superuser=True)
        logistics = UserFactory()
        _add_to_group(logistics, logistics_group)
        sig_admin = UserFactory()
        SIGAdmin.objects.create(user=sig_admin, group=sig, is_active=True)
        other_admin = UserFactory()
        SIGAdmin.objects.create(user=other_admin, group=other, is_active=True)
        regular = UserFactory()

        # staff / super / Logistics / admin-of-the-target-group may assign
        assert can_assign_to_owning_group(staff, sig) is True
        assert can_assign_to_owning_group(superu, sig) is True
        assert can_assign_to_owning_group(logistics, sig) is True
        assert can_assign_to_owning_group(sig_admin, sig) is True
        # admin of a *different* SIG, and plain regular users, may not
        assert can_assign_to_owning_group(other_admin, sig) is False
        assert can_assign_to_owning_group(regular, sig) is False


@pytest.mark.unit
class TestScopeQuerysetByOwnership:
    def _make_items(self):
        sig1 = Group.objects.create(name="SIG1")
        sig2 = Group.objects.create(name="SIG2")
        i1 = InventoryItemFactory(owning_group=sig1)
        i2 = InventoryItemFactory(owning_group=sig2)
        ispace = InventoryItemFactory(owning_group=None)
        return sig1, sig2, i1, i2, ispace

    def test_list_policy(self, logistics_group):
        sig1, _sig2, i1, i2, ispace = self._make_items()
        base = InventoryItem.objects.all()

        def scoped(user):
            return set(scope_queryset_by_ownership(base, user, policy=OwnershipVisibility.LIST))

        # staff / super / Logistics / regular / anon all see everything
        assert scoped(UserFactory(is_staff=True)) == {i1, i2, ispace}
        assert scoped(UserFactory(is_superuser=True)) == {i1, i2, ispace}
        logistics = UserFactory()
        _add_to_group(logistics, logistics_group)
        assert scoped(logistics) == {i1, i2, ispace}
        assert scoped(UserFactory()) == {i1, i2, ispace}
        assert scoped(AnonymousUser()) == {i1, i2, ispace}

        # SIG admin is narrowed to their own SIG
        sig1_admin = UserFactory()
        SIGAdmin.objects.create(user=sig1_admin, group=sig1, is_active=True)
        assert scoped(sig1_admin) == {i1}

    def test_restricted_policy(self, logistics_group):
        sig1, _sig2, i1, i2, ispace = self._make_items()
        base = InventoryItem.objects.all()

        def scoped(user):
            return set(
                scope_queryset_by_ownership(base, user, policy=OwnershipVisibility.RESTRICTED)
            )

        # staff / super / Logistics still see everything
        assert scoped(UserFactory(is_staff=True)) == {i1, i2, ispace}
        logistics = UserFactory()
        _add_to_group(logistics, logistics_group)
        assert scoped(logistics) == {i1, i2, ispace}

        # SIG admin is narrowed to their own SIG
        sig1_admin = UserFactory()
        SIGAdmin.objects.create(user=sig1_admin, group=sig1, is_active=True)
        assert scoped(sig1_admin) == {i1}

        # regular user sees ONLY space-owned rows under the restricted policy
        assert scoped(UserFactory()) == {ispace}

    def test_related_field_path(self):
        """The ``field`` param scopes a related queryset (e.g. reorder requests
        by their item's owning group)."""
        sig1 = Group.objects.create(name="SIG1")
        r_sig1 = ReorderRequestFactory(item=InventoryItemFactory(owning_group=sig1))
        r_space = ReorderRequestFactory(item=InventoryItemFactory(owning_group=None))

        from reorder_queue.models import ReorderRequest

        base = ReorderRequest.objects.all()
        sig1_admin = UserFactory()
        SIGAdmin.objects.create(user=sig1_admin, group=sig1, is_active=True)

        # SIG admin sees only requests for their SIG's items, regardless of policy
        assert set(
            scope_queryset_by_ownership(
                base,
                sig1_admin,
                policy=OwnershipVisibility.LIST,
                field="item__owning_group",
            )
        ) == {r_sig1}

        # regular user, restricted policy -> only space-owned items' requests
        assert set(
            scope_queryset_by_ownership(
                base,
                UserFactory(),
                policy=OwnershipVisibility.RESTRICTED,
                field="item__owning_group",
            )
        ) == {r_space}


@pytest.mark.unit
class TestOwnableModelMixin:
    """The shared mixin contributes the ownership fields + the ``is_user_*``
    delegators to both concrete models (behaviour preserved from the old
    per-model methods)."""

    def test_ownership_type_is_shared_single_definition(self):
        assert Asset.OwnershipType is InventoryItem.OwnershipType
        assert Asset.OwnershipType is OwnableModel.OwnershipType
        assert Asset.OwnershipType.GROUP == "group"
        assert InventoryItem.OwnershipType.SPACE == "space"

    def test_inherited_methods_on_both_models(self, logistics_group):
        sig = Group.objects.create(name="Robotics")
        staff = UserFactory(is_staff=True)
        regular = UserFactory()
        logistics = UserFactory()
        _add_to_group(logistics, logistics_group)
        sig_admin = UserFactory()
        SIGAdmin.objects.create(user=sig_admin, group=sig, is_active=True)

        asset = AssetFactory(owning_group=sig)
        item = InventoryItemFactory(owning_group=sig)
        space_asset = AssetFactory(owning_group=None)

        for obj in (asset, item):
            assert obj.is_user_admin(staff) is True
            assert obj.is_user_admin(regular) is False
            assert obj.is_user_in_logistics(logistics) is True
            assert obj.is_user_in_logistics(regular) is False
            assert obj.is_user_group_admin(sig_admin) is True
            assert obj.is_user_group_admin(regular) is False

        # space-owned object has no owning group -> not a group admin for anyone
        assert space_asset.is_user_group_admin(sig_admin) is False
