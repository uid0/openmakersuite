"""URL routes for the resilience app."""

from django.urls import path

from .views import ResilienceStatusView

app_name = "resilience"

urlpatterns = [
    path("status/", ResilienceStatusView.as_view(), name="status"),
]
