"""URL routing for the maker box app."""

from django.urls import include, path

from rest_framework.routers import DefaultRouter

from .views import MakerBoxViewSet, verify_scan

router = DefaultRouter()
router.register(r"", MakerBoxViewSet, basename="maker-box")

urlpatterns = [
    # Public QR-scan verification (AllowAny). The label QR encodes
    # `{FRONTEND_URL}/scan/makerbox/<bin>/<user>/` which calls this.
    path(
        "verify/<str:bin_id>/<str:username>/",
        verify_scan,
        name="maker-box-verify-scan",
    ),
    path("", include(router.urls)),
]
