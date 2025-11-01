"""
Models for dashboard configuration management.
"""

from django.db import models


class DashboardMessage(models.Model):
    """
    Configurable messages for TV Dashboard footer rotation.
    
    Allows dynamic management of footer messages without requiring
    environment variable changes or application restarts.
    """
    message = models.CharField(
        max_length=255,
        help_text="Message text to display in dashboard footer"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this message should be included in rotation"
    )
    order = models.PositiveIntegerField(
        default=0,
        help_text="Display order (lower numbers shown first)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['order', 'created_at']
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
        default=10,
        help_text="Seconds between message rotations"
    )
    
    # Display settings
    auto_refresh_seconds = models.PositiveIntegerField(
        default=30,
        help_text="Seconds between data refreshes"
    )
    
    # Custom styling (future enhancement)
    custom_css = models.TextField(
        blank=True,
        help_text="Optional custom CSS for dashboard styling"
    )
    
    # Operational settings
    is_maintenance_mode = models.BooleanField(
        default=False,
        help_text="Show maintenance message instead of normal dashboard"
    )
    maintenance_message = models.TextField(
        default="Dashboard temporarily unavailable for maintenance",
        help_text="Message to show during maintenance mode"
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
