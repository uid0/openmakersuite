"""
URL configuration for search app.
"""

from django.urls import path

from .views import get_recent_searches, global_search, save_recent_search

urlpatterns = [
    path("", global_search, name="global-search"),
    path("recent/", get_recent_searches, name="recent-searches"),
    path("recent/save/", save_recent_search, name="save-recent-search"),
]
