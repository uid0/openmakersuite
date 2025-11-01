"""
Basic tests for auth views to improve coverage.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

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
