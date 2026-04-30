"""URL routing for electrical_circuits API."""

from django.urls import include, path

from rest_framework.routers import DefaultRouter

from .views import (
    BreakerViewSet,
    LightSwitchViewSet,
    NetworkDropListReportView,
    NetworkDropViewSet,
    OutletViewSet,
    PanelDirectoryReportView,
)

router = DefaultRouter()
router.register(r"breakers", BreakerViewSet, basename="breaker")
router.register(r"outlets", OutletViewSet, basename="outlet")
router.register(r"light-switches", LightSwitchViewSet, basename="light-switch")
router.register(r"network-drops", NetworkDropViewSet, basename="network-drop")

urlpatterns = [
    path("", include(router.urls)),
    path(
        "reports/panel-directory.pdf",
        PanelDirectoryReportView.as_view(),
        name="electrical-panel-directory-pdf",
    ),
    path(
        "reports/network-drop-list.pdf",
        NetworkDropListReportView.as_view(),
        name="electrical-network-drop-list-pdf",
    ),
]
