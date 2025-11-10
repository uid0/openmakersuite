"""
URL configuration for customization app.
"""

from django.urls import path

from .views import site_settings

app_name = "customization"

urlpatterns = [
    path("settings/", site_settings, name="site-settings"),
]
