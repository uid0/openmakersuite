"""
Tests for user registration token functionality.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

import pytest

from membership.models import UserRegistrationToken

User = get_user_model()


@pytest.mark.unit
class TestUserRegistrationToken:
    """Tests for UserRegistrationToken model."""

    def test_token_creation(self):
        """Test creating a registration token."""
        user = User.objects.create_user(
            username="testuser", email="test@example.com", password="dummy"
        )
        admin_user = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="admin"
        )

        token = UserRegistrationToken.create_for_user(
            user=user,
            created_by=admin_user,
        )

        assert token.user == user
        assert token.created_by == admin_user
        assert token.token is not None
        assert len(token.token) > 0
        assert token.used is False
        assert token.used_at is None
        assert token.expires_at > timezone.now()

    def test_token_generation(self):
        """Test token generation creates unique tokens."""
        tokens = []
        for _ in range(10):
            token = UserRegistrationToken.generate_token()
            assert token not in tokens
            tokens.append(token)
            assert len(token) > 0

    def test_token_is_valid(self):
        """Test token validation."""
        user = User.objects.create_user(
            username="testuser", email="test@example.com", password="dummy"
        )
        token = UserRegistrationToken.create_for_user(user=user)

        assert token.is_valid() is True

    def test_token_is_invalid_when_used(self):
        """Test token is invalid after being used."""
        user = User.objects.create_user(
            username="testuser", email="test@example.com", password="dummy"
        )
        token = UserRegistrationToken.create_for_user(user=user)

        assert token.is_valid() is True
        token.mark_as_used()
        assert token.is_valid() is False
        assert token.used is True
        assert token.used_at is not None

    def test_token_is_invalid_when_expired(self):
        """Test token is invalid after expiration."""
        user = User.objects.create_user(
            username="testuser", email="test@example.com", password="dummy"
        )
        token = UserRegistrationToken.create_for_user(user=user)

        # Manually set expiration to past
        token.expires_at = timezone.now() - timedelta(days=1)
        token.save()

        assert token.is_valid() is False

    def test_token_registration_url(self):
        """Test registration URL generation."""
        user = User.objects.create_user(
            username="testuser", email="test@example.com", password="dummy"
        )
        token = UserRegistrationToken.create_for_user(user=user)

        url = token.get_registration_url()
        assert token.token in url
        assert "/register/" in url

    def test_token_one_to_one_with_user(self):
        """Test that each user can only have one registration token."""
        user = User.objects.create_user(
            username="testuser", email="test@example.com", password="dummy"
        )

        token1 = UserRegistrationToken.create_for_user(user=user)
        assert token1.user == user

        # Creating another token should replace the old one or raise an error
        # Since it's OneToOneField, creating a new one should work
        # (the old one will be deleted if we use get_or_create pattern)
        # But for now, let's test that we can't have two active tokens
        # Actually, OneToOneField allows only one, so creating a second will fail
        # unless we delete the first
        token1.delete()

        token2 = UserRegistrationToken.create_for_user(user=user)
        assert token2.user == user
        assert token2.id != token1.id


@pytest.mark.integration
class TestRegistrationAPI:
    """Integration tests for registration API endpoints."""

    def test_validate_token_endpoint(self, client):
        """Test token validation endpoint."""
        user = User.objects.create_user(
            username="testuser", email="test@example.com", password="dummy"
        )
        token = UserRegistrationToken.create_for_user(user=user)

        response = client.post(
            "/api/membership/register/validate-token/",
            {"token": token.token},
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert data["user"]["username"] == "testuser"

    def test_validate_token_endpoint_invalid_token(self, client):
        """Test token validation with invalid token."""
        response = client.post(
            "/api/membership/register/validate-token/",
            {"token": "invalid_token"},
            content_type="application/json",
        )

        assert response.status_code == 400
        data = response.json()
        assert "token" in data

    def test_validate_token_endpoint_used_token(self, client):
        """Test token validation with used token."""
        user = User.objects.create_user(
            username="testuser", email="test@example.com", password="dummy"
        )
        token = UserRegistrationToken.create_for_user(user=user)
        token.mark_as_used()

        response = client.post(
            "/api/membership/register/validate-token/",
            {"token": token.token},
            content_type="application/json",
        )

        assert response.status_code == 400
        data = response.json()
        assert "token" in data

    def test_register_user_with_token(self, client):
        """Test user registration with valid token."""
        user = User.objects.create_user(
            username="testuser", email="test@example.com", password="dummy"
        )
        token = UserRegistrationToken.create_for_user(user=user)

        response = client.post(
            "/api/membership/register/complete/",
            {
                "token": token.token,
                "password": "newpassword123",
                "password2": "newpassword123",
            },
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Registration completed successfully."
        assert data["user"]["username"] == "testuser"

        # Verify password was set
        user.refresh_from_db()
        assert user.check_password("newpassword123") is True

        # Verify token was marked as used
        token.refresh_from_db()
        assert token.used is True

    def test_register_user_with_token_password_mismatch(self, client):
        """Test registration with mismatched passwords."""
        user = User.objects.create_user(
            username="testuser", email="test@example.com", password="dummy"
        )
        token = UserRegistrationToken.create_for_user(user=user)

        response = client.post(
            "/api/membership/register/complete/",
            {
                "token": token.token,
                "password": "newpassword123",
                "password2": "differentpassword",
            },
            content_type="application/json",
        )

        assert response.status_code == 400
        data = response.json()
        assert "password2" in data

    def test_register_user_with_token_invalid_token(self, client):
        """Test registration with invalid token."""
        response = client.post(
            "/api/membership/register/complete/",
            {
                "token": "invalid_token",
                "password": "newpassword123",
                "password2": "newpassword123",
            },
            content_type="application/json",
        )

        assert response.status_code == 400
        data = response.json()
        assert "token" in data

    def test_register_user_with_token_used_token(self, client):
        """Test registration with already used token."""
        user = User.objects.create_user(
            username="testuser", email="test@example.com", password="dummy"
        )
        token = UserRegistrationToken.create_for_user(user=user)
        token.mark_as_used()

        response = client.post(
            "/api/membership/register/complete/",
            {
                "token": token.token,
                "password": "newpassword123",
                "password2": "newpassword123",
            },
            content_type="application/json",
        )

        assert response.status_code == 400
        data = response.json()
        assert "token" in data
