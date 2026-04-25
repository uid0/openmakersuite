"""URL routing for the maker box app."""

from django.urls import include, path

from rest_framework.routers import DefaultRouter

from .views import MakerBoxViewSet

router = DefaultRouter()
router.register(r"", MakerBoxViewSet, basename="maker-box")

urlpatterns = [
    path("", include(router.urls)),
]
