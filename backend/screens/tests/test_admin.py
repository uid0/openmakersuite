"""
Admin tests for the screens app.
"""

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.utils.crypto import get_random_string

import pytest

from screens.admin import ScreenAdmin
from screens.models import Screen
from screens.tests.factories import ScreenFactory

User = get_user_model()


@pytest.fixture
def admin_request(db):
    user = User.objects.create_superuser(
        username="screens-admin",
        email="admin@example.test",
        password=get_random_string(24),
    )
    request = RequestFactory().get("/admin/")
    request.user = user
    return request


@pytest.fixture
def screen_admin():
    return ScreenAdmin(Screen, AdminSite())


@pytest.mark.django_db
class TestScreenAdminPreviewLink:
    def test_link_includes_slug_and_token(self, screen_admin):
        screen = ScreenFactory(slug="lobby")
        html = screen_admin.preview_link(screen)
        assert f"/kiosk/{screen.slug}?token={screen.access_token}" in html
        assert 'target="_blank"' in html
        assert 'rel="noopener"' in html

    def test_link_placeholder_when_unsaved(self, screen_admin):
        unsaved = Screen(slug="not-yet", name="Not Yet")
        result = screen_admin.preview_link(unsaved)
        assert "save the screen" in result.lower()
        assert "<a " not in result

    def test_preview_field_hidden_on_create_form(self, screen_admin, admin_request):
        fields = screen_admin.get_fields(admin_request, obj=None)
        assert "preview_link" not in fields

    def test_preview_field_first_on_change_form(self, screen_admin, admin_request):
        screen = ScreenFactory()
        fields = screen_admin.get_fields(admin_request, obj=screen)
        assert fields[0] == "preview_link"

    def test_change_view_renders_preview_link(self, admin_request, client):
        password = get_random_string(24)
        admin = User.objects.create_superuser(
            username="change-view-admin",
            email="cv@example.test",
            password=password,
        )
        client.force_login(admin)
        screen = ScreenFactory(slug="kiosk-test")

        resp = client.get(f"/admin/screens/screen/{screen.pk}/change/")

        assert resp.status_code == 200
        body = resp.content.decode()
        assert f"/kiosk/{screen.slug}?token={screen.access_token}" in body
        assert "Open kiosk preview" in body
