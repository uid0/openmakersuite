"""URL routing for electrical_circuits API."""

from django.urls import include, path

from rest_framework.routers import DefaultRouter

from .safety_views import (
    AssetPowerChainView,
    PowerBreakerTripImpactView,
    PowerCircuitLoadView,
    PowerPanelListView,
    PowerPanelTopologyView,
)
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


# Safety query endpoints (oms-b25 [4/7]). Mounted by config.urls under
# /api/electrical/ so the URLs match the AC-1..AC-4 paths exactly.
safety_urlpatterns = [
    path(
        "panels/",
        PowerPanelListView.as_view(),
        name="electrical-panel-list",
    ),
    path(
        "breakers/<int:pk>/trip-impact/",
        PowerBreakerTripImpactView.as_view(),
        name="electrical-breaker-trip-impact",
    ),
    path(
        "circuits/<int:pk>/load/",
        PowerCircuitLoadView.as_view(),
        name="electrical-circuit-load",
    ),
    path(
        "panels/<int:pk>/topology/",
        PowerPanelTopologyView.as_view(),
        name="electrical-panel-topology",
    ),
]


# Mounted by config.urls under /api/assets/ so AC-4's literal path
# (``/api/assets/<id>/power-chain/``) resolves without straying into
# the inventory app's CRUD routes.
asset_power_chain_urlpatterns = [
    path(
        "<uuid:pk>/power-chain/",
        AssetPowerChainView.as_view(),
        name="asset-power-chain",
    ),
]
