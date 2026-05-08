"""Tests for the `is_staff_or_sig_admin` helper and the matching DRF
permission classes (gh #374)."""

from types import SimpleNamespace

from django.contrib.auth.models import AnonymousUser, Group

import pytest

from membership.models import SIGAdmin
from membership.permissions import (
    IsAuthenticatedOrStaffSigAdminWrite,
    IsStaffOrSigAdmin,
)
from membership.tests.factories import UserFactory
from membership.utils import is_staff_or_sig_admin

pytestmark = pytest.mark.django_db


def _request(user, method="GET"):
    return SimpleNamespace(user=user, method=method)


@pytest.mark.unit
class TestIsStaffOrSigAdminHelper:
    def test_returns_false_for_anonymous(self):
        assert is_staff_or_sig_admin(AnonymousUser()) is False

    def test_returns_false_for_none(self):
        assert is_staff_or_sig_admin(None) is False

    def test_returns_false_for_unauthenticated_member(self):
        user = UserFactory(is_active=False)
        assert is_staff_or_sig_admin(user) is False

    def test_true_for_staff(self):
        assert is_staff_or_sig_admin(UserFactory(is_staff=True)) is True

    def test_true_for_superuser_even_without_staff_flag(self):
        assert is_staff_or_sig_admin(UserFactory(is_superuser=True)) is True

    def test_true_for_logistics_member(self):
        logistics = Group.objects.create(name="Logistics")
        user = UserFactory()
        user.groups.add(logistics)
        assert is_staff_or_sig_admin(user) is True

    def test_true_for_sig_admin(self):
        user = UserFactory()
        sig = Group.objects.create(name="3D Printing SIG")
        SIGAdmin.objects.create(user=user, group=sig)
        assert is_staff_or_sig_admin(user) is True

    def test_false_for_plain_authenticated_user(self):
        user = UserFactory()
        assert is_staff_or_sig_admin(user) is False

    def test_false_when_user_is_in_unrelated_group(self):
        user = UserFactory()
        user.groups.add(Group.objects.create(name="Volunteers"))
        assert is_staff_or_sig_admin(user) is False


@pytest.mark.unit
class TestIsStaffOrSigAdminPermission:
    """Both read and write are gated."""

    permission = IsStaffOrSigAdmin()

    def test_blocks_anonymous(self):
        assert self.permission.has_permission(_request(AnonymousUser()), None) is False

    def test_blocks_volunteer_read(self):
        assert self.permission.has_permission(_request(UserFactory(), "GET"), None) is False

    def test_blocks_volunteer_write(self):
        assert self.permission.has_permission(_request(UserFactory(), "POST"), None) is False

    def test_allows_staff_read(self):
        assert (
            self.permission.has_permission(_request(UserFactory(is_staff=True), "GET"), None)
            is True
        )

    def test_allows_staff_write(self):
        assert (
            self.permission.has_permission(_request(UserFactory(is_staff=True), "POST"), None)
            is True
        )

    def test_allows_sig_admin_read(self):
        user = UserFactory()
        SIGAdmin.objects.create(user=user, group=Group.objects.create(name="Wood SIG"))
        assert self.permission.has_permission(_request(user, "GET"), None) is True


@pytest.mark.unit
class TestIsAuthenticatedOrStaffSigAdminWrite:
    """Read for any authenticated user; write only for staff / SIG admin."""

    permission = IsAuthenticatedOrStaffSigAdminWrite()

    def test_blocks_anonymous_read(self):
        assert self.permission.has_permission(_request(AnonymousUser(), "GET"), None) is False

    def test_allows_volunteer_read(self):
        assert self.permission.has_permission(_request(UserFactory(), "GET"), None) is True

    def test_blocks_volunteer_write(self):
        assert self.permission.has_permission(_request(UserFactory(), "POST"), None) is False

    def test_blocks_volunteer_patch(self):
        assert self.permission.has_permission(_request(UserFactory(), "PATCH"), None) is False

    def test_allows_staff_write(self):
        assert (
            self.permission.has_permission(_request(UserFactory(is_staff=True), "POST"), None)
            is True
        )

    def test_allows_sig_admin_write(self):
        user = UserFactory()
        SIGAdmin.objects.create(user=user, group=Group.objects.create(name="Metal SIG"))
        assert self.permission.has_permission(_request(user, "POST"), None) is True

    def test_allows_logistics_member_write(self):
        user = UserFactory()
        user.groups.add(Group.objects.create(name="Logistics"))
        assert self.permission.has_permission(_request(user, "PUT"), None) is True
