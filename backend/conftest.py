"""
Pytest configuration and fixtures for the entire test suite.
"""

from io import BytesIO

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils.crypto import get_random_string

import pytest
from PIL import Image
from rest_framework.test import APIClient


@pytest.fixture
def api_client():
    """Fixture to provide an API client for testing."""
    return APIClient()


@pytest.fixture
def authenticated_client(db):
    """Fixture to provide an authenticated API client."""
    client = APIClient()
    random_password = get_random_string(24)
    user = User.objects.create_user(
        username="testuser", email="test@example.com", password=random_password
    )
    client.force_authenticate(user=user)
    return client, user


@pytest.fixture
def admin_user(db):
    """Fixture to create an admin user."""
    return User.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password=get_random_string(24),
    )


@pytest.fixture
def sample_image():
    """Fixture to create a sample image for testing."""
    image = Image.new("RGB", (100, 100), color="red")
    image_io = BytesIO()
    image.save(image_io, format="JPEG")
    image_io.seek(0)
    return SimpleUploadedFile(
        name="test_image.jpg", content=image_io.read(), content_type="image/jpeg"
    )


@pytest.fixture(autouse=True)
def enable_db_access_for_all_tests(db):
    """Automatically enable database access for all tests."""
    pass


@pytest.fixture(autouse=True)
def use_locmem_cache(settings):
    """Route cache operations to local memory to avoid Redis during tests."""

    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "test-cache",
        }
    }


@pytest.fixture
def mock_celery_task(mocker):
    """Mock Celery tasks to run synchronously in tests."""

    def _mock_task(task_path):
        return mocker.patch(task_path, side_effect=lambda *args, **kwargs: None)

    return _mock_task
