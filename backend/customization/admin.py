"""
Admin configuration for customization app.
"""

from django.contrib import admin
from django.utils.html import format_html

from .models import SiteSettings


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    """Admin interface for site customization settings."""

    def has_add_permission(self, request):
        """Only allow one instance."""
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        """Prevent deletion of the only instance."""
        return False

    list_display = [
        "site_name",
        "logo_preview",
        "primary_color_display",
        "updated_at",
    ]
    readonly_fields = [
        "created_at",
        "updated_at",
        "logo_preview",
        "favicon_preview",
    ]

    fieldsets = (
        (
            "Basic Information",
            {
                "fields": (
                    "site_name",
                    "site_tagline",
                )
            },
        ),
        (
            "Branding",
            {
                "fields": (
                    "logo",
                    "logo_preview",
                    "logo_alt_text",
                    "favicon",
                    "favicon_preview",
                )
            },
        ),
        (
            "Colors",
            {
                "fields": (
                    "primary_color",
                    "secondary_color",
                ),
                "description": "Enter hex color codes (e.g., #007cba)",
            },
        ),
        (
            "Custom Styling",
            {
                "fields": ("custom_css",),
                "description": "Custom CSS to inject into the frontend. Use with caution.",
            },
        ),
        (
            "Dashboard Customization",
            {
                "fields": (
                    "dashboard_title",
                    "dashboard_subtitle",
                    "show_logo_on_dashboard",
                )
            },
        ),
        (
            "Contact Information",
            {
                "fields": (
                    "contact_email",
                    "contact_phone",
                    "website_url",
                )
            },
        ),
        (
            "Footer",
            {
                "fields": ("footer_text",),
                "description": "HTML is supported in footer text",
            },
        ),
        (
            "Tax Receipt Information",
            {
                "fields": (
                    "organization_address",
                    "organization_ein",
                    "authorized_signature_name",
                    "authorized_signature_title",
                ),
                "description": "Information used for generating 501(c)(3) tax receipts",
            },
        ),
        (
            "Metadata",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description="Logo Preview")
    def logo_preview(self, obj):
        """Display a preview of the logo."""
        if obj.logo:
            return format_html(
                '<img src="{}" style="max-height: 100px; max-width: 200px;" />',
                obj.logo.url,
            )
        return "No logo uploaded"

    @admin.display(description="Favicon Preview")
    def favicon_preview(self, obj):
        """Display a preview of the favicon."""
        if obj.favicon:
            return format_html(
                '<img src="{}" style="max-height: 64px; max-width: 64px;" />',
                obj.favicon.url,
            )
        return "No favicon uploaded"

    @admin.display(description="Primary Color")
    def primary_color_display(self, obj):
        """Display the primary color as a colored square."""
        return format_html(
            '<div style="width: 30px; height: 30px; background-color: {}; border: 1px solid #ccc; display: inline-block; vertical-align: middle; margin-right: 8px;"></div> {}',
            obj.primary_color,
            obj.primary_color,
        )
