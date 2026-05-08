"""URL routes for the analytics app."""

from django.urls import path

from .views import AnalyticsPulseView

app_name = "analytics"

urlpatterns = [
    path("pulse/", AnalyticsPulseView.as_view(), name="pulse"),
]
