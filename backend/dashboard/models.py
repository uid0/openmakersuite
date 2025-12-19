"""
Models for dashboard configuration management.
"""

from django.contrib.auth import get_user_model
from django.db import models

User = get_user_model()


class DashboardMessage(models.Model):
    """
    Configurable messages for TV Dashboard footer rotation.

    Allows dynamic management of footer messages without requiring
    environment variable changes or application restarts.
    """

    message = models.CharField(
        max_length=255, help_text="Message text to display in dashboard footer"
    )
    is_active = models.BooleanField(
        default=True, help_text="Whether this message should be included in rotation"
    )
    order = models.PositiveIntegerField(
        default=0, help_text="Display order (lower numbers shown first)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "created_at"]
        verbose_name = "Dashboard Message"
        verbose_name_plural = "Dashboard Messages"

    def __str__(self):
        status = "Active" if self.is_active else "Inactive"
        return f"{self.message} ({status})"


class DashboardConfig(models.Model):
    """
    Global configuration settings for the TV Dashboard.
    """

    # Message rotation settings
    rotation_interval_seconds = models.PositiveIntegerField(
        default=10, help_text="Seconds between message rotations"
    )

    # Display settings
    auto_refresh_seconds = models.PositiveIntegerField(
        default=30, help_text="Seconds between data refreshes"
    )

    # Custom styling (future enhancement)
    custom_css = models.TextField(blank=True, help_text="Optional custom CSS for dashboard styling")

    # Operational settings
    is_maintenance_mode = models.BooleanField(
        default=False, help_text="Show maintenance message instead of normal dashboard"
    )
    maintenance_message = models.TextField(
        default="Dashboard temporarily unavailable for maintenance",
        help_text="Message to show during maintenance mode",
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Dashboard Configuration"
        verbose_name_plural = "Dashboard Configuration"

    def __str__(self):
        return f"Dashboard Config (updated {self.updated_at.strftime('%Y-%m-%d %H:%M')})"

    @classmethod
    def get_config(cls):
        """Get or create the dashboard configuration singleton."""
        config, created = cls.objects.get_or_create(id=1)
        return config


class DashboardWidget(models.Model):
    """
    User-specific dashboard widget configuration.

    Stores widget layout, position, and settings for each user's dashboard.
    """

    WIDGET_TYPES = [
        ("low_stock", "Low Stock"),
        ("pending_reorders", "Pending Reorders"),
        ("asset_problems", "Asset Problems"),
        ("qr_scans", "QR Scans"),
        ("deliveries", "Deliveries"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="dashboard_widgets",
        help_text="User who owns this widget configuration",
    )
    widget_type = models.CharField(
        max_length=50,
        choices=WIDGET_TYPES,
        help_text="Type of widget",
    )
    position_x = models.PositiveIntegerField(
        default=0, help_text="X position in grid (0-based)"
    )
    position_y = models.PositiveIntegerField(
        default=0, help_text="Y position in grid (0-based)"
    )
    width = models.PositiveIntegerField(
        default=2, help_text="Width in grid units"
    )
    height = models.PositiveIntegerField(
        default=2, help_text="Height in grid units"
    )
    is_visible = models.BooleanField(
        default=True, help_text="Whether this widget is visible"
    )
    order = models.PositiveIntegerField(
        default=0, help_text="Display order (lower numbers shown first)"
    )
    settings = models.JSONField(
        default=dict,
        blank=True,
        help_text="Widget-specific settings (JSON)",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user", "order", "position_y", "position_x"]
        unique_together = [["user", "widget_type"]]
        verbose_name = "Dashboard Widget"
        verbose_name_plural = "Dashboard Widgets"
        indexes = [
            models.Index(fields=["user", "is_visible"]),
            models.Index(fields=["user", "widget_type"]),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.get_widget_type_display()}"

    @classmethod
    def create_default_widgets(cls, user):
        """
        Create default widget configuration for a new user.

        Returns a list of created widgets.
        """
        default_layout = [
            {"type": "low_stock", "x": 0, "y": 0, "w": 2, "h": 2, "order": 0},
            {"type": "pending_reorders", "x": 2, "y": 0, "w": 2, "h": 2, "order": 1},
            {"type": "asset_problems", "x": 0, "y": 2, "w": 2, "h": 2, "order": 2},
            {"type": "qr_scans", "x": 2, "y": 2, "w": 2, "h": 3, "order": 3},
            {"type": "deliveries", "x": 0, "y": 4, "w": 2, "h": 3, "order": 4},
        ]

        widgets = []
        for layout in default_layout:
            widget, created = cls.objects.get_or_create(
                user=user,
                widget_type=layout["type"],
                defaults={
                    "position_x": layout["x"],
                    "position_y": layout["y"],
                    "width": layout["w"],
                    "height": layout["h"],
                    "order": layout["order"],
                    "is_visible": True,
                },
            )
            widgets.append(widget)

        return widgets
