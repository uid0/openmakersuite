"""
Admin configuration for search app.
"""

from django.contrib import admin

from .models import RecentSearch


@admin.register(RecentSearch)
class RecentSearchAdmin(admin.ModelAdmin):
    """Admin interface for recent searches."""

    list_display = ["user", "query", "result_type", "result_title", "searched_at"]
    list_filter = ["result_type", "searched_at"]
    search_fields = ["query", "result_title", "user__username"]
    readonly_fields = ["searched_at"]
    date_hierarchy = "searched_at"
