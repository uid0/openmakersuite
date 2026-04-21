"""
Pytest configuration and fixtures for the entire test suite.
"""

from io import BytesIO

from django.contrib.auth import get_user_model

User = get_user_model()
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


@pytest.fixture(autouse=True, scope="session")
def use_locmem_cache():
    """Route cache operations to local memory to avoid Redis during tests."""
    from django.conf import settings as django_settings
    from django.core.signals import setting_changed

    new_caches = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "test-cache",
        }
    }
    django_settings.CACHES = new_caches
    # Fire setting_changed so Django resets cached cache-handler state
    # (clear_cache_handlers in django.test.signals). Without this, any
    # caches[...] access made before this fixture runs (e.g. at import
    # time in a xdist worker) keeps a stale backend reference.
    setting_changed.send(sender=None, setting="CACHES", value=new_caches, enter=True)


@pytest.fixture(autouse=True)
def clear_locmem_cache():
    """Clear the locmem cache between tests so counters (rate limits,
    throttles) don't bleed across tests under the session-scoped cache."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def mock_celery_task(mocker):
    """Mock Celery tasks to run synchronously in tests."""

    def _mock_task(task_path):
        return mocker.patch(task_path, side_effect=lambda *args, **kwargs: None)

    return _mock_task


@pytest.fixture(autouse=True)
def configure_celery_for_tests(settings):
    """Configure Celery to run tasks synchronously in tests."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True
    settings.CELERY_BROKER_URL = "memory://"
    settings.CELERY_RESULT_BACKEND = "cache"
    settings.CELERY_CACHE_BACKEND = "memory"


@pytest.fixture(autouse=True, scope="session")
def ensure_auth_user_groups_table(django_db_setup, django_db_blocker):
    """Ensure auth_user_groups table exists for the session.

    Runs once after Django sets up the test database instead of on every test.
    """
    from django.db import connection

    with django_db_blocker.unblock():
        with connection.cursor() as cursor:
            if connection.vendor == "sqlite":
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name='auth_user_groups';"
                )
                table_exists = cursor.fetchone() is not None
                if not table_exists:
                    cursor.execute("""
                        CREATE TABLE auth_user_groups (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            user_id INTEGER NOT NULL,
                            group_id INTEGER NOT NULL,
                            UNIQUE(user_id, group_id),
                            FOREIGN KEY (user_id) REFERENCES auth_user(id),
                            FOREIGN KEY (group_id) REFERENCES auth_group(id)
                        );
                        CREATE INDEX auth_user_groups_user_id_idx
                            ON auth_user_groups(user_id);
                        CREATE INDEX auth_user_groups_group_id_idx
                            ON auth_user_groups(group_id);
                        """)
            elif connection.vendor == "postgresql":
                cursor.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_schema = 'public'
                        AND table_name = 'auth_user_groups'
                    );
                    """)
                table_exists = cursor.fetchone()[0]
                if not table_exists:
                    cursor.execute("""
                        CREATE TABLE auth_user_groups (
                            id SERIAL PRIMARY KEY,
                            user_id INTEGER NOT NULL,
                            group_id INTEGER NOT NULL,
                            UNIQUE(user_id, group_id),
                            FOREIGN KEY (user_id) REFERENCES auth_user(id) ON DELETE CASCADE,
                            FOREIGN KEY (group_id) REFERENCES auth_group(id) ON DELETE CASCADE
                        );
                        CREATE INDEX auth_user_groups_user_id_idx
                            ON auth_user_groups(user_id);
                        CREATE INDEX auth_user_groups_group_id_idx
                            ON auth_user_groups(group_id);
                        """)
