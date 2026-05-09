"""URL routes for the analytics app."""

from django.urls import path

from .views import AnalyticsPulseView, AnalyticsShareView

app_name = "analytics"

urlpatterns = [
    path("pulse/", AnalyticsPulseView.as_view(), name="pulse"),
    path("share/", AnalyticsShareView.as_view(), name="share"),
]
