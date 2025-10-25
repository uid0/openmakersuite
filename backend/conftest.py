"""
Pytest configuration and fixtures for the entire test suite.
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth.models import User
from rest_framework.test import APIClient
from PIL import Image
from io import BytesIO


@pytest.fixture
def api_client():
    """Fixture to provide an API client for testing."""
    return APIClient()


@pytest.fixture
def authenticated_client(db):
    """Fixture to provide an authenticated API client."""
    client = APIClient()
    user = User.objects.create_user(
        username="testuser", email="test@example.com", password="testpass123"
    )
    client.force_authenticate(user=user)
    return client, user


@pytest.fixture
def admin_user(db):
    """Fixture to create an admin user."""
    return User.objects.create_superuser(
        username="admin", email="admin@example.com", password="admin123"
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


@pytest.fixture
def mock_celery_task(mocker):
    """Mock Celery tasks to run synchronously in tests."""

    def _mock_task(task_path):
        return mocker.patch(task_path, side_effect=lambda *args, **kwargs: None)

    return _mock_task
