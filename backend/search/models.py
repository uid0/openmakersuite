"""
Models for search functionality.
"""

from django.contrib.auth import get_user_model
from django.db import models

User = get_user_model()


class RecentSearch(models.Model):
    """
    Store recent searches for users to enable quick access and cross-device sync.
    """

    RESULT_TYPE_CHOICES = [
        ("inventory", "Inventory Item"),
        ("asset", "Asset"),
        ("purchase_order", "Purchase Order"),
        ("supplier", "Supplier"),
        ("location", "Location"),
    ]

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="recent_searches", null=True, blank=True
    )
    query = models.CharField(max_length=200, help_text="Search query text")
    result_type = models.CharField(
        max_length=20, choices=RESULT_TYPE_CHOICES, help_text="Type of result"
    )
    result_id = models.CharField(max_length=100, help_text="ID of the result")
    result_title = models.CharField(
        max_length=200, help_text="Title/name of the result for display"
    )
    searched_at = models.DateTimeField(auto_now_add=True, help_text="When the search occurred")

    class Meta:
        ordering = ["-searched_at"]
        indexes = [
            models.Index(fields=["user", "-searched_at"]),
            models.Index(fields=["user", "result_type", "-searched_at"]),
        ]

    def __str__(self):
        user_str = self.user.username if self.user else "Anonymous"
        return f"{user_str} searched '{self.query}' -> {self.result_type}:{self.result_id}"
