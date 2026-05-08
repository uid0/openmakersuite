from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse

import pytest

from membership.models import Membership

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture
def member_user():
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


class TestRegisterUserErrorEnvelope:
    def test_register_requires_username(self, api_client):
        response = api_client.post(
            reverse("register"),
            {"email": "newmaker@example.com", "password": "brand-new-pass"},
            format="json",
        )

        assert response.status_code == 400
        assert response.json() == {
            "error": {
                "code": "validation_failed",
                "message": "Username is required",
            }
        }

    def test_register_rejects_short_username(self, api_client):
        response = api_client.post(
            reverse("register"),
            {"username": "ab", "email": "", "password": "whatever"},
            format="json",
        )

        assert response.status_code == 400
        assert response.json()["error"] == {
            "code": "validation_failed",
            "message": "Username must be at least 3 characters long",
        }

    def test_register_rejects_duplicate_username_with_conflict_code(self, api_client, member_user):
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
        assert response.json()["error"]["code"] == "conflict"
        assert response.json()["error"]["message"] == (
            "Username already exists. Please choose another."
        )

    def test_register_rejects_invalid_email(self, api_client):
        response = api_client.post(
            reverse("register"),
            {
                "username": "newmaker",
                "email": "not-an-email",
                "password": "brand-new-pass",
            },
            format="json",
        )

        assert response.status_code == 400
        assert response.json()["error"] == {
            "code": "validation_failed",
            "message": "Please enter a valid email address",
        }


class TestLoginUserErrorEnvelope:
    def test_login_requires_username_and_password(self, api_client):
        response = api_client.post(reverse("login"), {}, format="json")

        assert response.status_code == 400
        assert response.json()["error"] == {
            "code": "validation_failed",
            "message": "Username and password are required",
        }

    def test_login_rejects_bad_credentials(self, api_client, member_user):
        response = api_client.post(
            reverse("login"),
            {"username": "member", "password": "wrong"},
            format="json",
        )

        assert response.status_code == 401
        assert response.json()["error"] == {
            "code": "authentication_failed",
            "message": "Invalid credentials",
        }

    def test_login_rejects_user_without_login_permission(self, api_client, member_user):
        with patch.object(User, "can_login", return_value=False):
            response = api_client.post(
                reverse("login"),
                {"username": "member", "password": "correct-horse-battery"},
                format="json",
            )

        assert response.status_code == 403
        assert response.json()["error"] == {
            "code": "permission_denied",
            "message": "User does not have an active membership or required role",
        }


class TestRefreshTokenErrorEnvelope:
    def test_refresh_requires_refresh_token(self, api_client):
        response = api_client.post(reverse("refresh"), {}, format="json")

        assert response.status_code == 400
        assert response.json()["error"] == {
            "code": "validation_failed",
            "message": "Refresh token is required",
        }

    def test_refresh_rejects_invalid_token(self, api_client):
        response = api_client.post(
            reverse("refresh"),
            {"refresh": "not-a-token"},
            format="json",
        )

        assert response.status_code == 401
        assert response.json()["error"] == {
            "code": "authentication_failed",
            "message": "Invalid refresh token",
        }
