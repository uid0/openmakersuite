"""URL configuration for the vendors API."""

from django.urls import include, path

from config.routers import ApiRouter

from .views import VendorViewSet

router = ApiRouter()
router.register(r"vendors", VendorViewSet, basename="vendor")

urlpatterns = [
    path("", include(router.urls)),
]
