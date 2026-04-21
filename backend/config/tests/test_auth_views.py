"""
Tests for the unified authentication endpoints.

These cover the contract that logging in once via the REST endpoint
simultaneously authenticates the Django admin (session) and the REST API
(JWT), plus the matching logout behaviour.
"""

from django.contrib.auth import SESSION_KEY, get_user_model
from django.urls import reverse

import pytest
from rest_framework.test import APIClient

from membership.models import Membership

User = get_user_model()


@pytest.fixture
def member_user(db):
    """User with an active membership — allowed to log in."""
    user = User.objects.create_user(
        username="member",
        email="member@example.com",
        password="correct-horse-battery",
    )
    membership = Membership.objects.create(
        membership_type=Membership.MEMBERSHIP_TYPE_MONTHLY,
        status=Membership.STATUS_ACTIVE,
    )
    membership.users.add(user)
    return user


@pytest.fixture
def staff_user(db):
    """Staff user (no membership needed)."""
    return User.objects.create_user(
        username="staffer",
        email="staff@example.com",
        password="staff-secret-42",
        is_staff=True,
    )


@pytest.fixture
def plain_user(db):
    """Active user without membership or role — must not be allowed to log in."""
    return User.objects.create_user(
        username="nomember",
        email="nomember@example.com",
        password="no-membership-pass",
    )


class TestUnifiedLogin:
    def test_login_returns_jwt_tokens(self, api_client, member_user):
        response = api_client.post(
            reverse("login"),
            {"username": "member", "password": "correct-horse-battery"},
            format="json",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["access"]
        assert body["refresh"]
        assert body["username"] == "member"
        assert body["email"] == "member@example.com"
        assert body["is_staff"] is False
        assert body["is_superuser"] is False

    def test_login_establishes_django_session(self, api_client, member_user):
        """A successful login must set the session auth key so admin + DRF
        browsable API are authenticated by the same request."""
        response = api_client.post(
            reverse("login"),
            {"username": "member", "password": "correct-horse-battery"},
            format="json",
        )
        assert response.status_code == 200
        # APIClient persists the session; the auth key is set by auth.login.
        assert str(member_user.pk) == api_client.session[SESSION_KEY]

    def test_login_session_authorises_drf_without_jwt(self, api_client, member_user, settings):
        """Session cookie alone (no Authorization header) should reach
        authenticated REST endpoints."""
        settings.DEVELOPMENT_MODE = False
        api_client.post(
            reverse("login"),
            {"username": "member", "password": "correct-horse-battery"},
            format="json",
        )
        # Profile endpoint requires authentication.
        profile = api_client.get("/api/membership/profile/me/")
        assert profile.status_code == 200

    def test_login_rejects_bad_password(self, api_client, member_user):
        response = api_client.post(
            reverse("login"),
            {"username": "member", "password": "wrong"},
            format="json",
        )
        assert response.status_code == 401
        assert SESSION_KEY not in api_client.session

    def test_login_requires_username_and_password(self, api_client):
        response = api_client.post(reverse("login"), {}, format="json")
        assert response.status_code == 400

    def test_login_rejects_user_without_membership_or_role(self, api_client, plain_user):
        response = api_client.post(
            reverse("login"),
            {"username": "nomember", "password": "no-membership-pass"},
            format="json",
        )
        assert response.status_code == 403
        assert SESSION_KEY not in api_client.session

    def test_login_allows_staff_without_membership(self, api_client, staff_user):
        response = api_client.post(
            reverse("login"),
            {"username": "staffer", "password": "staff-secret-42"},
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["is_staff"] is True
        assert str(staff_user.pk) == api_client.session[SESSION_KEY]

    def test_login_rejects_inactive_user(self, api_client, member_user):
        member_user.is_active = False
        member_user.save(update_fields=["is_active"])
        response = api_client.post(
            reverse("login"),
            {"username": "member", "password": "correct-horse-battery"},
            format="json",
        )
        assert response.status_code == 401


class TestLogout:
    def test_logout_destroys_session(self, api_client, member_user):
        api_client.post(
            reverse("login"),
            {"username": "member", "password": "correct-horse-battery"},
            format="json",
        )
        assert SESSION_KEY in api_client.session

        response = api_client.post(reverse("logout"))
        assert response.status_code == 200
        assert SESSION_KEY not in api_client.session

    def test_logout_is_idempotent(self, api_client):
        """Calling logout with no session is allowed."""
        response = api_client.post(reverse("logout"))
        assert response.status_code == 200


class TestUnifiedRegister:
    def test_register_logs_in_new_user(self, api_client, db):
        response = api_client.post(
            reverse("register"),
            {
                "username": "newmaker",
                "email": "newmaker@example.com",
                "password": "brand-new-pass",
            },
            format="json",
        )
        assert response.status_code == 201
        body = response.json()
        assert body["access"]
        assert body["refresh"]
        assert body["username"] == "newmaker"
        # Session established so admin/DRF browsable API are usable immediately.
        user = User.objects.get(username="newmaker")
        assert str(user.pk) == api_client.session[SESSION_KEY]

    def test_register_rejects_short_username(self, api_client, db):
        response = api_client.post(
            reverse("register"),
            {"username": "ab", "email": "", "password": "whatever"},
            format="json",
        )
        assert response.status_code == 400

    def test_register_rejects_duplicate_username(self, api_client, member_user):
        response = api_client.post(
            reverse("register"),
            {
                "username": "member",
                "email": "dup@example.com",
                "password": "whatever",
            },
            format="json",
        )
        assert response.status_code == 400


class TestRefreshEndpoint:
    def test_refresh_returns_new_access_token(self, api_client, member_user):
        login_response = api_client.post(
            reverse("login"),
            {"username": "member", "password": "correct-horse-battery"},
            format="json",
        )
        refresh = login_response.json()["refresh"]

        # Use a clean client so we don't accidentally authenticate via session.
        fresh = APIClient()
        response = fresh.post(reverse("refresh"), {"refresh": refresh}, format="json")
        assert response.status_code == 200
        assert response.json()["access"]

    def test_refresh_rejects_missing_token(self, api_client):
        response = api_client.post(reverse("refresh"), {}, format="json")
        assert response.status_code == 400

    def test_refresh_rejects_invalid_token(self, api_client):
        response = api_client.post(reverse("refresh"), {"refresh": "not-a-token"}, format="json")
        assert response.status_code == 401
