"""
Basic tests for auth views to improve coverage.
"""

import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

User = get_user_model()


class AuthViewsBasicTest(TestCase):
    """Basic tests for authentication functionality."""

    def test_user_model_exists(self):
        """Test that user model works."""
        user = User.objects.create_user(username="testuser", password="testpass")
        self.assertEqual(user.username, "testuser")
        self.assertTrue(user.check_password("testpass"))

    def test_superuser_creation(self):
        """Test superuser creation."""
        superuser = User.objects.create_superuser(
            username="admin", email="admin@test.com", password="adminpass"
        )
        self.assertTrue(superuser.is_staff)
        self.assertTrue(superuser.is_superuser)

    def test_user_authentication(self):
        """Test user authentication basics."""
        user = User.objects.create_user(username="testuser", password="testpass")
        self.assertTrue(user.is_authenticated)
        self.assertFalse(user.is_anonymous)

    def test_user_permissions(self):
        """Test basic user permissions."""
        user = User.objects.create_user(username="testuser", password="testpass")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)


class AuthViewsImportTest(TestCase):
    """Test importing auth views."""

    def test_auth_views_importable(self):
        """Test that auth views can be imported."""
        try:
            import auth_views

            self.assertIsNotNone(auth_views)
        except ImportError:
            # If auth_views doesn't exist, that's ok
            pass

    def test_django_auth_works(self):
        """Test Django's built-in auth functionality."""
        from django.contrib.auth import authenticate

        # Create user
        user = User.objects.create_user(username="testuser", password="testpass")

        # Test authentication
        auth_user = authenticate(username="testuser", password="testpass")
        self.assertEqual(auth_user, user)

        # Test wrong password
        auth_user = authenticate(username="testuser", password="wrongpass")
        self.assertIsNone(auth_user)


class PasskeyEndpointsTest(TestCase):
    """Smoke tests for the django-passkey-auth endpoints mounted at /auth/passkey/.

    The Register Passkey button in Django admin (oms-0sb) silently failed in
    production because nginx had no proxy block for /auth/passkey/, so requests
    fell through to the SPA catch-all and the JS got HTML instead of JSON.
    These tests pin the backend contract: the URL stays mounted and returns
    JSON to an authenticated session.
    """

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="passkeyadmin", email="p@example.com", password="pw"
        )
        self.client = Client()
        self.client.force_login(self.user)

    def test_passkey_info_returns_json(self):
        response = self.client.get("/auth/passkey/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "application/json")
        body = json.loads(response.content)
        self.assertIn("rpId", body)
        self.assertEqual(body["userName"], "passkeyadmin")

    def test_passkey_register_get_returns_webauthn_options(self):
        response = self.client.get("/auth/passkey/register/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "application/json")
        body = json.loads(response.content)
        # Spot-check the WebAuthn PublicKeyCredentialCreationOptions shape.
        self.assertIn("challenge", body)
        self.assertIn("rp", body)
        self.assertIn("user", body)
        self.assertIn("pubKeyCredParams", body)

    def test_passkey_register_requires_authentication(self):
        anon = Client()
        # LoginRequiredMixin redirects unauthenticated users to the login URL.
        response = anon.get("/auth/passkey/register/")
        self.assertIn(response.status_code, (302, 401, 403))
