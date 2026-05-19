"""URL routing for the climate API."""

from django.urls import include, path

from rest_framework.routers import DefaultRouter

from .views import ThermostatViewSet

router = DefaultRouter()
router.register(r"thermostats", ThermostatViewSet, basename="thermostat")

urlpatterns = [
    path("", include(router.urls)),
]
