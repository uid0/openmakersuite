"""
URLs for location check-in API.
"""

from django.urls import include, path

from rest_framework.routers import DefaultRouter

from .views import (
    LocationCheckInViewSet,
    LocationFeedbackViewSet,
    LocationTaskViewSet,
    SecurityReportViewSet,
    TopLocationsReportView,
    TrafficReportView,
    location_ping_webhook,
)

router = DefaultRouter()
router.register(r"checkins", LocationCheckInViewSet, basename="location-checkin")
router.register(r"feedback", LocationFeedbackViewSet, basename="location-feedback")
router.register(r"security-reports", SecurityReportViewSet, basename="security-report")
router.register(r"tasks", LocationTaskViewSet, basename="location-task")

urlpatterns = [
    path("webhook/", location_ping_webhook, name="location-ping-webhook"),
    path(
        "reports/traffic/",
        TrafficReportView.as_view(),
        name="location-traffic-report",
    ),
    path(
        "reports/top/",
        TopLocationsReportView.as_view(),
        name="location-top-report",
    ),
    path("", include(router.urls)),
]
