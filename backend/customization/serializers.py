"""
Serializers for customization API.
"""

from rest_framework import serializers

from .models import SiteSettings


class SiteSettingsSerializer(serializers.ModelSerializer):
    """Serializer for site settings."""

    logo_url = serializers.SerializerMethodField()
    favicon_url = serializers.SerializerMethodField()

    class Meta:
        model = SiteSettings
        fields = [
            "site_name",
            "site_tagline",
            "logo_url",
            "logo_alt_text",
            "favicon_url",
            "primary_color",
            "secondary_color",
            "footer_text",
            "contact_email",
            "contact_phone",
            "website_url",
            "dashboard_title",
            "dashboard_subtitle",
            "show_logo_on_dashboard",
        ]
        read_only_fields = fields

    def get_logo_url(self, obj):
        """Get the full URL for the logo."""
        if obj.logo:
            request = self.context.get("request")
            if request:
                return request.build_absolute_uri(obj.logo.url)
            return obj.logo.url
        return None

    def get_favicon_url(self, obj):
        """Get the full URL for the favicon."""
        if obj.favicon:
            request = self.context.get("request")
            if request:
                return request.build_absolute_uri(obj.favicon.url)
            return obj.favicon.url
        return None


class SiteSettingsUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating site settings (superuser only)."""

    logo_url = serializers.SerializerMethodField()
    favicon_url = serializers.SerializerMethodField()

    class Meta:
        model = SiteSettings
        fields = [
            "site_name",
            "site_tagline",
            "logo",
            "logo_url",
            "logo_alt_text",
            "favicon",
            "favicon_url",
            "primary_color",
            "secondary_color",
            "footer_text",
            "contact_email",
            "contact_phone",
            "website_url",
            "dashboard_title",
            "dashboard_subtitle",
            "show_logo_on_dashboard",
        ]
        read_only_fields = ["logo_url", "favicon_url"]

    def validate_logo(self, value):
        """Handle logo deletion - empty string means delete."""
        # If value is an empty string, return None to delete the logo
        if value == "":
            return None
        return value

    def get_logo_url(self, obj):
        """Get the full URL for the logo."""
        if obj.logo:
            request = self.context.get("request")
            if request:
                return request.build_absolute_uri(obj.logo.url)
            return obj.logo.url
        return None

    def get_favicon_url(self, obj):
        """Get the full URL for the favicon."""
        if obj.favicon:
            request = self.context.get("request")
            if request:
                return request.build_absolute_uri(obj.favicon.url)
            return obj.favicon.url
        return None
