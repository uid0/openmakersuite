"""
Models for site customization.
"""

from django.core.exceptions import ValidationError
from django.db import models


class SiteSettings(models.Model):
    """
    Site-wide customization settings.

    This is a singleton model - only one instance should exist.
    Use SiteSettings.get() to get or create the single instance.
    """

    # Basic branding
    site_name = models.CharField(
        max_length=200,
        default="Makerspace Inventory",
        help_text="The name of your makerspace or organization",
    )
    site_tagline = models.CharField(
        max_length=300,
        blank=True,
        help_text="Optional tagline or subtitle for your site",
    )

    # Logo
    logo = models.ImageField(
        upload_to="customization/logos/",
        blank=True,
        null=True,
        help_text="Upload your organization's logo. Recommended size: 200x50px or similar aspect ratio",
    )
    logo_alt_text = models.CharField(
        max_length=200,
        blank=True,
        default="Site Logo",
        help_text="Alt text for the logo image (for accessibility)",
    )
    favicon = models.ImageField(
        upload_to="customization/favicons/",
        blank=True,
        null=True,
        help_text="Favicon for the site (should be square, e.g., 32x32px or 64x64px)",
    )

    # Colors and theme
    primary_color = models.CharField(
        max_length=7,
        default="#007cba",
        help_text="Primary brand color (hex code, e.g., #007cba)",
    )
    secondary_color = models.CharField(
        max_length=7,
        default="#417690",
        help_text="Secondary brand color (hex code)",
    )

    # Footer and contact
    footer_text = models.TextField(
        blank=True,
        help_text="Text to display in the footer (supports HTML)",
    )
    contact_email = models.EmailField(
        blank=True,
        help_text="Contact email address to display on the site",
    )
    contact_phone = models.CharField(
        max_length=20,
        blank=True,
        help_text="Contact phone number",
    )
    website_url = models.URLField(
        blank=True,
        help_text="Main website URL for your organization",
    )

    # Dashboard customization
    dashboard_title = models.CharField(
        max_length=200,
        blank=True,
        help_text="Custom title for the dashboard (overrides site name on dashboard)",
    )
    dashboard_subtitle = models.CharField(
        max_length=300,
        blank=True,
        help_text="Subtitle text for the dashboard",
    )
    show_logo_on_dashboard = models.BooleanField(
        default=True,
        help_text="Whether to display the logo on the dashboard",
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Site Settings"
        verbose_name_plural = "Site Settings"
        ordering = ["-updated_at"]

    def __str__(self):
        return f"Site Settings for {self.site_name}"

    def clean(self):
        """Ensure only one instance exists."""
        if not self.pk:
            if SiteSettings.objects.exists():
                raise ValidationError(
                    "Only one SiteSettings instance is allowed. "
                    "Please edit the existing instance instead."
                )

    def save(self, *args, **kwargs):
        """Override save to ensure singleton pattern."""
        self.full_clean()
        # If this is the first instance, allow it
        if not SiteSettings.objects.exists() or self.pk:
            super().save(*args, **kwargs)
        else:
            # If another instance exists, update it instead
            existing = SiteSettings.objects.first()
            for field in self._meta.fields:
                if field.name not in ["id", "created_at", "updated_at"]:
                    setattr(existing, field.name, getattr(self, field.name))
            existing.save(*args, **kwargs)

    @classmethod
    def get(cls):
        """Get or create the single SiteSettings instance."""
        obj = cls.objects.first()
        if not obj:
            obj = cls.objects.create()
        return obj
