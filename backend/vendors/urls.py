"""URL configuration for the vendors API."""

from django.urls import include, path

from rest_framework.routers import DefaultRouter

from .views import VendorViewSet

router = DefaultRouter()
router.register(r"vendors", VendorViewSet, basename="vendor")

urlpatterns = [
    path("", include(router.urls)),
]
