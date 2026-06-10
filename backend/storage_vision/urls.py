"""URL routing for the storage_vision app — slice 5."""

from __future__ import annotations

from django.urls import include, path

from rest_framework.routers import DefaultRouter

from .views import (
    VisionAreaViewSet,
    VisionCameraViewSet,
    VisionCaptureViewSet,
    VisionObservationViewSet,
    VisionSlotViewSet,
)

app_name = "storage_vision"

router = DefaultRouter()
router.register(r"areas", VisionAreaViewSet, basename="area")
router.register(r"slots", VisionSlotViewSet, basename="slot")
router.register(r"cameras", VisionCameraViewSet, basename="camera")
router.register(r"captures", VisionCaptureViewSet, basename="capture")
router.register(r"observations", VisionObservationViewSet, basename="observation")

urlpatterns = [
    path("", include(router.urls)),
]
