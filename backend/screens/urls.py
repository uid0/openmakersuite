"""
URLs for the screens app.
"""

from django.urls import include, path

from rest_framework.routers import DefaultRouter

from . import views, weather_views

router = DefaultRouter()
router.register(r"screens", views.ScreenViewSet, basename="screen")
router.register(r"blocks", views.ScreenContentBlockViewSet, basename="screen-block")
router.register(r"messages", views.SystemMessageViewSet, basename="system-message")

urlpatterns = [
    path("kiosk/<slug:slug>/", views.kiosk_payload, name="kiosk-payload"),
    path("kiosk/<slug:slug>/heartbeat/", views.kiosk_heartbeat, name="kiosk-heartbeat"),
    path("weather/current/", weather_views.current_weather, name="weather-current"),
    path("", include(router.urls)),
]
