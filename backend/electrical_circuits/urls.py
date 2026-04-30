"""URL routing for electrical_circuits API."""

from django.urls import include, path

from rest_framework.routers import DefaultRouter

from .views import BreakerViewSet, LightSwitchViewSet, NetworkDropViewSet, OutletViewSet

router = DefaultRouter()
router.register(r"breakers", BreakerViewSet, basename="breaker")
router.register(r"outlets", OutletViewSet, basename="outlet")
router.register(r"light-switches", LightSwitchViewSet, basename="light-switch")
router.register(r"network-drops", NetworkDropViewSet, basename="network-drop")

urlpatterns = [
    path("", include(router.urls)),
]
