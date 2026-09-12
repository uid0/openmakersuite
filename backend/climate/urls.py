"""URL routing for the climate API."""

from django.urls import include, path

from config.routers import ApiRouter

from .views import ThermostatViewSet

router = ApiRouter()
router.register(r"thermostats", ThermostatViewSet, basename="thermostat")

urlpatterns = [
    path("", include(router.urls)),
]
