"""URL routing for the LOTO API (oms-78j)."""

from django.urls import include, path

from rest_framework.routers import DefaultRouter

from .views import AssetEnergySourceViewSet, AssetLOTORequirementsView, LOTODeviceViewSet

router = DefaultRouter()
router.register(r"devices", LOTODeviceViewSet, basename="loto-device")
router.register(r"energy-sources", AssetEnergySourceViewSet, basename="loto-energy-source")

urlpatterns = [
    path("", include(router.urls)),
    path(
        "assets/<uuid:asset_id>/loto-requirements/",
        AssetLOTORequirementsView.as_view(),
        name="asset-loto-requirements",
    ),
]
