"""
Unit tests for membership models.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError

import pytest

from membership.models import Membership

User = get_user_model()


@pytest.mark.unit
class TestUserModel:
    """Tests for the custom User model."""

    def test_user_creation(self):
        """Test creating a user instance with all fields."""
        user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
            handle="testhandle",
            active_directory_username="ad_testuser",
            badge_number="BADGE001",
            discord_username="discord_test",
            discourse_username="discourse_test",
            is_board_member=True,
            is_officer=False,
            is_director=False,
        )

        assert user.username == "testuser"
        assert user.handle == "testhandle"
        assert user.active_directory_username == "ad_testuser"
        assert user.badge_number == "BADGE001"
        assert user.discord_username == "discord_test"
        assert user.discourse_username == "discourse_test"
        assert user.is_board_member is True
        assert user.is_officer is False
        assert user.is_director is False

    def test_user_handle_unique(self):
        """Test that handle must be unique."""
        User.objects.create_user(
            username="user1",
            email="user1@example.com",
            password="pass",
            handle="uniquehandle",
        )

        with pytest.raises((IntegrityError, ValidationError)):
            User.objects.create_user(
                username="user2",
                email="user2@example.com",
                password="pass",
                handle="uniquehandle",
            )

    def test_user_badge_number_unique(self):
        """Test that badge_number must be unique."""
        User.objects.create_user(
            username="user1",
            email="user1@example.com",
            password="pass",
            badge_number="BADGE001",
        )

        with pytest.raises((IntegrityError, ValidationError)):
            User.objects.create_user(
                username="user2",
                email="user2@example.com",
                password="pass",
                badge_number="BADGE001",
            )

    def test_user_can_login_with_active_membership(self):
        """Test that user can login if they have an active membership."""
        user = User.objects.create_user(
            username="member", email="member@example.com", password="pass"
        )
        membership = Membership.objects.create(
            membership_type=Membership.MEMBERSHIP_TYPE_MONTHLY,
            status=Membership.STATUS_ACTIVE,
        )
        membership.users.add(user)

        assert user.can_login() is True

    def test_user_can_login_with_staff_status(self):
        """Test that staff users can always login."""
        user = User.objects.create_user(
            username="staff", email="staff@example.com", password="pass", is_staff=True
        )

        assert user.can_login() is True

    def test_user_can_login_with_superuser_status(self):
        """Test that superusers can always login."""
        user = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="pass"
        )

        assert user.can_login() is True

    def test_user_can_login_with_board_member_status(self):
        """Test that board members can always login."""
        user = User.objects.create_user(
            username="board",
            email="board@example.com",
            password="pass",
            is_board_member=True,
        )

        assert user.can_login() is True

    def test_user_can_login_with_director_status(self):
        """Test that directors can always login."""
        user = User.objects.create_user(
            username="director",
            email="director@example.com",
            password="pass",
            is_director=True,
        )

        assert user.can_login() is True

    def test_user_cannot_login_without_active_membership(self):
        """Test that user cannot login without active membership or special status."""
        user = User.objects.create_user(
            username="regular", email="regular@example.com", password="pass"
        )

        assert user.can_login() is False

    def test_user_cannot_login_with_inactive_membership(self):
        """Test that user cannot login with inactive membership."""
        user = User.objects.create_user(
            username="inactive", email="inactive@example.com", password="pass"
        )
        membership = Membership.objects.create(
            membership_type=Membership.MEMBERSHIP_TYPE_MONTHLY,
            status=Membership.STATUS_INACTIVE,
        )
        membership.users.add(user)

        assert user.can_login() is False


@pytest.mark.unit
class TestMembershipModel:
    """Tests for the Membership model."""

    def test_membership_creation(self):
        """Test creating a membership instance."""
        membership = Membership.objects.create(
            membership_type=Membership.MEMBERSHIP_TYPE_ANNUAL,
            status=Membership.STATUS_ACTIVE,
            start_date=date.today(),
            notes="Test membership",
        )

        assert membership.membership_type == Membership.MEMBERSHIP_TYPE_ANNUAL
        assert membership.status == Membership.STATUS_ACTIVE
        assert membership.start_date == date.today()
        assert membership.notes == "Test membership"

    def test_membership_defaults(self):
        """Test membership default values."""
        membership = Membership.objects.create()

        assert membership.membership_type == Membership.MEMBERSHIP_TYPE_MONTHLY
        assert membership.status == Membership.STATUS_INACTIVE

    def test_membership_str_representation(self):
        """Test membership string representation."""
        user1 = User.objects.create_user(
            username="user1", email="user1@example.com", password="pass"
        )
        user2 = User.objects.create_user(
            username="user2", email="user2@example.com", password="pass"
        )

        membership = Membership.objects.create(
            membership_type=Membership.MEMBERSHIP_TYPE_MONTHLY,
            status=Membership.STATUS_ACTIVE,
        )
        membership.users.add(user1, user2)

        str_repr = str(membership)
        assert "Monthly" in str_repr
        assert "Active" in str_repr
        assert "user1" in str_repr
        assert "user2" in str_repr

    def test_membership_str_with_many_users(self):
        """Test membership string representation truncates when many users."""
        membership = Membership.objects.create(
            membership_type=Membership.MEMBERSHIP_TYPE_ANNUAL,
            status=Membership.STATUS_ACTIVE,
        )

        # Add more than 3 users
        for i in range(5):
            user = User.objects.create_user(
                username=f"user{i}", email=f"user{i}@example.com", password="pass"
            )
            membership.users.add(user)

        str_repr = str(membership)
        assert "..." in str_repr  # Should truncate with ...

    def test_membership_is_active_property(self):
        """Test membership is_active property."""
        active_membership = Membership.objects.create(
            membership_type=Membership.MEMBERSHIP_TYPE_MONTHLY,
            status=Membership.STATUS_ACTIVE,
        )
        inactive_membership = Membership.objects.create(
            membership_type=Membership.MEMBERSHIP_TYPE_MONTHLY,
            status=Membership.STATUS_INACTIVE,
        )

        assert active_membership.is_active is True
        assert inactive_membership.is_active is False

    def test_membership_many_users(self):
        """Test membership can have multiple users (family/business accounts)."""
        user1 = User.objects.create_user(
            username="parent1", email="parent1@example.com", password="pass"
        )
        user2 = User.objects.create_user(
            username="parent2", email="parent2@example.com", password="pass"
        )
        user3 = User.objects.create_user(
            username="child", email="child@example.com", password="pass"
        )

        membership = Membership.objects.create(
            membership_type=Membership.MEMBERSHIP_TYPE_ANNUAL,
            status=Membership.STATUS_ACTIVE,
        )
        membership.users.add(user1, user2, user3)

        assert membership.users.count() == 3
        assert user1 in membership.users.all()
        assert user2 in membership.users.all()
        assert user3 in membership.users.all()

    def test_membership_choices(self):
        """Test membership type and status choices."""
        # Test all membership types
        for choice_value, choice_label in Membership.MEMBERSHIP_TYPE_CHOICES:
            membership = Membership.objects.create(membership_type=choice_value)
            assert membership.get_membership_type_display() == choice_label

        # Test all status choices
        for choice_value, choice_label in Membership.STATUS_CHOICES:
            membership = Membership.objects.create(status=choice_value)
            assert membership.get_status_display() == choice_label

    def test_membership_ordering(self):
        """Test memberships are ordered by created_at descending."""
        membership1 = Membership.objects.create()
        membership2 = Membership.objects.create()
        membership3 = Membership.objects.create()

        memberships = Membership.objects.all()
        # Most recent should be first
        assert memberships[0].id == membership3.id
        assert memberships[1].id == membership2.id
        assert memberships[2].id == membership1.id
