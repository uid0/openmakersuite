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
)

router = DefaultRouter()
router.register(r"checkins", LocationCheckInViewSet, basename="location-checkin")
router.register(r"feedback", LocationFeedbackViewSet, basename="location-feedback")
router.register(r"security-reports", SecurityReportViewSet, basename="security-report")
router.register(r"tasks", LocationTaskViewSet, basename="location-task")

urlpatterns = [
    path("", include(router.urls)),
]
