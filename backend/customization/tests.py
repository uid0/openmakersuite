"""
Tests for customization app.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

import pytest
from rest_framework.test import APIClient

from .audit import diff_audited_fields, record_event, snapshot
from .models import SiteSettings, SiteSettingsAuditEvent

User = get_user_model()


@pytest.mark.unit
class TestSiteSettingsModel:
    """Test SiteSettings model."""

    def test_singleton_get_singleton_creates_and_reuses(self, db):
        """get_singleton() creates the row once and returns it on subsequent calls."""
        settings1 = SiteSettings.objects.get_singleton()
        settings2 = SiteSettings.objects.get_singleton()
        assert settings1.pk == settings2.pk
        assert SiteSettings.objects.count() == 1

    def test_get_delegates_to_get_singleton(self, db):
        """The .get() compatibility wrapper returns the same singleton row."""
        via_get = SiteSettings.get()
        via_manager = SiteSettings.objects.get_singleton()
        assert via_get.pk == via_manager.pk
        assert SiteSettings.objects.count() == 1

    def test_singleton_prevent_multiple_instances(self, db):
        """Only one instance may exist — a second fails validation."""
        SiteSettings.objects.get_singleton()  # Create first instance

        # Try to create another instance directly
        with pytest.raises(ValidationError):
            SiteSettings().full_clean()

    def test_saving_second_new_instance_raises_instead_of_redirecting(self, db):
        """Saving a *new* second instance raises rather than silently redirecting.

        The old save() copied fields onto the existing row when a second
        instance was saved; that surprising redirect was removed (gh #887,
        AC-3). The existing row must be left untouched.
        """
        first = SiteSettings.objects.get_singleton()
        first.site_name = "Original"
        first.save()

        with pytest.raises(ValidationError):
            SiteSettings(site_name="Second").save()

        assert SiteSettings.objects.count() == 1
        first.refresh_from_db()
        assert first.site_name == "Original"

    def test_default_values(self, db):
        """Test default values for SiteSettings."""
        settings = SiteSettings.get()
        assert settings.site_name == "Makerspace Inventory"
        assert settings.primary_color == "#007cba"
        assert settings.secondary_color == "#417690"
        assert settings.show_logo_on_dashboard is True

    def test_str_representation(self, db):
        """Test string representation of SiteSettings."""
        settings = SiteSettings.get()
        settings.site_name = "Test Makerspace"
        settings.save()
        assert str(settings) == "Site Settings for Test Makerspace"

    def test_update_existing_instance(self, db):
        """Fetch → mutate → save keeps the same row (no new instance)."""
        settings = SiteSettings.objects.get_singleton()
        settings.site_name = "Updated Name"
        settings.save()

        # Get again and verify update
        updated = SiteSettings.objects.get_singleton()
        assert updated.site_name == "Updated Name"
        assert updated.pk == settings.pk
        assert SiteSettings.objects.count() == 1


@pytest.mark.integration
class TestSiteSettingsAPI:
    """Test SiteSettings API endpoints."""

    def test_get_site_settings_public_access(self, api_client, db):
        """Test that site settings endpoint is publicly accessible."""
        # Create settings
        settings = SiteSettings.get()
        settings.site_name = "Test Site"
        settings.site_tagline = "Test Tagline"
        settings.save()

        # Make request without authentication
        response = api_client.get("/api/customization/settings/")
        assert response.status_code == 200
        data = response.json()
        assert data["site_name"] == "Test Site"
        assert data["site_tagline"] == "Test Tagline"

    def test_get_site_settings_with_logo(self, api_client, db, sample_image):
        """Test site settings with logo returns logo URL."""
        settings = SiteSettings.get()
        settings.logo = sample_image
        settings.save()

        response = api_client.get("/api/customization/settings/")
        assert response.status_code == 200
        data = response.json()
        assert data["logo_url"] is not None
        assert "test_image.jpg" in data["logo_url"] or "media" in data["logo_url"]

    def test_get_site_settings_without_logo(self, api_client, db):
        """Test site settings without logo returns null logo URL."""
        settings = SiteSettings.get()
        settings.logo = None
        settings.save()

        response = api_client.get("/api/customization/settings/")
        assert response.status_code == 200
        data = response.json()
        assert data["logo_url"] is None

    def test_get_site_settings_defaults(self, api_client, db):
        """Test that default values are returned when no settings exist."""
        # Ensure settings exist with defaults
        SiteSettings.get()

        response = api_client.get("/api/customization/settings/")
        assert response.status_code == 200
        data = response.json()
        assert data["site_name"] == "Makerspace Inventory"
        assert data["primary_color"] == "#007cba"
        assert data["show_logo_on_dashboard"] is True

    def test_get_site_settings_with_favicon(self, api_client, db, sample_image):
        """Test site settings with favicon returns favicon URL."""
        settings = SiteSettings.get()
        settings.favicon = sample_image
        settings.save()

        response = api_client.get("/api/customization/settings/")
        assert response.status_code == 200
        data = response.json()
        assert data["favicon_url"] is not None
        assert "test_image.jpg" in data["favicon_url"] or "media" in data["favicon_url"]

    def test_get_site_settings_all_fields(self, api_client, db):
        """Test that all fields are returned in the API response."""
        settings = SiteSettings.get()
        settings.site_name = "Test Site"
        settings.site_tagline = "Test Tagline"
        settings.primary_color = "#ff0000"
        settings.secondary_color = "#00ff00"
        settings.contact_email = "test@example.com"
        settings.contact_phone = "123-456-7890"
        settings.website_url = "https://example.com"
        settings.footer_text = "<p>Footer HTML</p>"
        settings.dashboard_title = "Custom Dashboard"
        settings.dashboard_subtitle = "Custom Subtitle"
        settings.show_logo_on_dashboard = False
        settings.save()

        response = api_client.get("/api/customization/settings/")
        assert response.status_code == 200
        data = response.json()
        assert data["site_name"] == "Test Site"
        assert data["site_tagline"] == "Test Tagline"
        assert data["primary_color"] == "#ff0000"
        assert data["secondary_color"] == "#00ff00"
        assert data["contact_email"] == "test@example.com"
        assert data["contact_phone"] == "123-456-7890"
        assert data["website_url"] == "https://example.com"
        assert data["footer_text"] == "<p>Footer HTML</p>"
        assert data["dashboard_title"] == "Custom Dashboard"
        assert data["dashboard_subtitle"] == "Custom Subtitle"
        assert data["show_logo_on_dashboard"] is False


@pytest.mark.django_db
class TestSiteSettingsAudit:
    """Test SiteSettings audit helpers and API integration."""

    def test_settings_update_records_audit_event(self):
        """PATCH with a real change records one audit event."""
        user = User.objects.create_user(
            username="superuser",
            password="testpass123",
            is_staff=True,
            is_superuser=True,
        )
        client = APIClient()
        client.force_authenticate(user=user)
        settings = SiteSettings.get()

        response = client.patch("/api/customization/settings/", {"site_name": "New Name"})

        assert response.status_code == 200
        assert SiteSettingsAuditEvent.objects.count() == 1

        event = SiteSettingsAuditEvent.objects.get()
        assert event.action == SiteSettingsAuditEvent.ACTION_SETTINGS_UPDATE
        assert event.actor == user
        assert event.site_settings == settings
        assert event.metadata["changes"]["site_name"]["before"] == "Makerspace Inventory"
        assert event.metadata["changes"]["site_name"]["after"] == "New Name"

    def test_settings_update_no_event_on_empty_diff(self):
        """PATCH with an unchanged value should not record an event."""
        user = User.objects.create_user(
            username="superuser-noop",
            password="testpass123",
            is_staff=True,
            is_superuser=True,
        )
        client = APIClient()
        client.force_authenticate(user=user)
        settings = SiteSettings.get()

        response = client.patch(
            "/api/customization/settings/",
            {"site_name": settings.site_name},
        )

        assert response.status_code == 200
        assert SiteSettingsAuditEvent.objects.count() == 0

    def test_settings_update_unauthenticated_no_event(self):
        """Unauthenticated PATCH is rejected and does not record an event."""
        SiteSettings.get()
        client = APIClient()

        response = client.patch("/api/customization/settings/", {"site_name": "Blocked"})

        assert response.status_code == 401
        assert SiteSettingsAuditEvent.objects.count() == 0

    def test_settings_update_non_superuser_no_event(self):
        """Non-superuser staff cannot update settings or create audit rows."""
        user = User.objects.create_user(
            username="staff-user",
            password="testpass123",
            is_staff=True,
            is_superuser=False,
        )
        client = APIClient()
        client.force_authenticate(user=user)
        SiteSettings.get()

        response = client.patch("/api/customization/settings/", {"site_name": "Blocked"})

        assert response.status_code == 403
        assert SiteSettingsAuditEvent.objects.count() == 0

    def test_diff_audited_fields_returns_empty_when_unchanged(self):
        """Diff helper returns an empty dict when audited fields match."""
        settings = SiteSettings.get()
        before = snapshot(settings)

        assert diff_audited_fields(before, settings) == {}

    def test_diff_audited_fields_summarizes_file_field_by_filename(self):
        """Diff helper reduces file-field changes to a filename string."""
        settings = SiteSettings.get()
        before = snapshot(settings)
        settings.logo = SimpleUploadedFile(
            "audit-logo.png",
            b"fake-image-bytes",
            content_type="image/png",
        )

        changes = diff_audited_fields(before, settings)

        assert changes["logo"]["before"] is None
        assert isinstance(changes["logo"]["after"], str)
        assert changes["logo"]["after"].endswith("audit-logo.png")

    def test_record_event_with_anonymous_actor_creates_row_with_null_actor(self):
        """record_event stores a null actor when called anonymously."""
        settings = SiteSettings.get()

        event = record_event(
            action=SiteSettingsAuditEvent.ACTION_SETTINGS_UPDATE,
            site_settings=settings,
            actor=None,
        )

        assert SiteSettingsAuditEvent.objects.count() == 1
        assert event.actor is None
