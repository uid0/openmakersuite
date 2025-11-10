"""
Tests for customization app.
"""

from django.core.exceptions import ValidationError

import pytest

from .models import SiteSettings


@pytest.mark.unit
class TestSiteSettingsModel:
    """Test SiteSettings model."""

    def test_singleton_get_or_create(self, db):
        """Test that get() returns the same instance."""
        settings1 = SiteSettings.get()
        settings2 = SiteSettings.get()
        assert settings1.pk == settings2.pk
        assert SiteSettings.objects.count() == 1

    def test_singleton_prevent_multiple_instances(self, db):
        """Test that only one instance can exist."""
        SiteSettings.get()  # Create first instance

        # Try to create another instance directly
        with pytest.raises(ValidationError):
            SiteSettings().full_clean()

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
        """Test that updating an existing instance works."""
        settings = SiteSettings.get()
        settings.site_name = "Updated Name"
        settings.save()

        # Get again and verify update
        updated = SiteSettings.get()
        assert updated.site_name == "Updated Name"
        assert updated.pk == settings.pk


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
