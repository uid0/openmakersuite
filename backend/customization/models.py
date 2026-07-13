"""
Models for site customization.
"""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class SiteSettingsManager(models.Manager):
    """Manager exposing the explicit singleton accessor for :class:`SiteSettings`."""

    def get_singleton(self) -> "SiteSettings":
        """Return the single settings row, creating it with defaults on first access.

        This is the explicit creation path for the singleton (gh #887). It
        replaces the old ``save()`` field-copy redirect: creating the row is no
        longer a surprising side effect of saving a second instance — callers
        ask for the singleton and always get exactly one row back.
        """
        obj = self.first()
        if obj is None:
            obj = self.create()
        return obj


class SiteSettings(models.Model):
    """
    Site-wide customization settings.

    This is a singleton model - only one instance should exist.
    Use ``SiteSettings.objects.get_singleton()`` (or the ``SiteSettings.get()``
    compatibility wrapper) to get or create the single instance.
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

    # Tax receipt information
    organization_address = models.TextField(
        blank=True,
        help_text="Organization address for tax receipts",
    )
    organization_ein = models.CharField(
        max_length=20,
        blank=True,
        help_text="Employer Identification Number (EIN) for tax receipts",
    )
    authorized_signature_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Name of authorized signatory for tax receipts",
    )
    authorized_signature_title = models.CharField(
        max_length=200,
        blank=True,
        help_text="Title of authorized signatory for tax receipts",
    )

    # PM auto-bundling — when a WorkOrder is created against a
    # MaintenanceItem and this is > 0, the WorkOrderViewSet bundles
    # every other active PM on the same asset whose next_due_at falls
    # within the window (or is already overdue) into the same WO.
    # 0 disables the feature (default — back-compat with the
    # pre-bundling world). Live-editable from the admin site-settings
    # form so a maintainer can flip the window without redeploying.
    pm_auto_bundle_due_within_days = models.PositiveIntegerField(
        default=0,
        help_text=(
            "When creating a work order against a PM, also roll in every "
            "other active PM on the same asset that is due (or overdue) "
            "within this many days. 0 disables auto-bundling."
        ),
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = SiteSettingsManager()

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
        """Validate (which enforces the singleton via ``clean()``) then persist.

        The old override redirected a second-instance save onto the existing
        row by copying fields — a surprising side effect. Singleton *creation*
        now lives in :meth:`SiteSettingsManager.get_singleton`; ``save()`` just
        validates and writes. ``clean()`` still raises on a second instance, so
        the one-row guarantee is preserved (gh #887, AC-3).
        """
        self.full_clean()
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        """Get or create the single SiteSettings instance.

        Thin compatibility wrapper over
        :meth:`SiteSettingsManager.get_singleton` so existing callers keep
        working unchanged.
        """
        return cls.objects.get_singleton()


class SiteSettingsAuditEvent(models.Model):
    """Append-only audit log for SiteSettings updates.

    Captures actor, action, settings reference, notes, and a per-field
    diff in metadata. Rows are written by
    ``customization.audit.record_event`` and never updated or deleted by
    application code.

    Per gh #358 / #334. Mirrors the per-domain audit tables in
    #352-#357.

    File fields (``logo``, ``favicon``) are diffed by filename only —
    the binary content is never stored in the audit row.
    """

    ACTION_SETTINGS_UPDATE = "settings_update"

    ACTION_CHOICES = [
        (ACTION_SETTINGS_UPDATE, "Site settings updated"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="site_settings_audit_actions",
        help_text="User who performed the action; null for system-initiated events.",
    )
    action = models.CharField(max_length=32, choices=ACTION_CHOICES)
    site_settings = models.ForeignKey(
        SiteSettings,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    notes = models.TextField(blank=True)
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Action-specific payload. For settings_update, includes a "
            "'changes' dict mapping field name -> {before, after}. "
            "File fields are summarized by filename only — binary "
            "content is never stored here."
        ),
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["site_settings", "-created_at"], name="settings_audit_ss_idx"),
            models.Index(fields=["actor", "-created_at"], name="settings_audit_actor_idx"),
            models.Index(fields=["action", "-created_at"], name="settings_audit_action_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.action} @ {self.created_at:%Y-%m-%d %H:%M}"
