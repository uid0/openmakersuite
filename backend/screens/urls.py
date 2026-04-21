"""
URLs for the screens app.
"""

from django.urls import include, path

from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r"screens", views.ScreenViewSet, basename="screen")
router.register(r"blocks", views.ScreenContentBlockViewSet, basename="screen-block")
router.register(r"messages", views.SystemMessageViewSet, basename="system-message")

urlpatterns = [
    path("kiosk/<slug:slug>/", views.kiosk_payload, name="kiosk-payload"),
    path("kiosk/<slug:slug>/heartbeat/", views.kiosk_heartbeat, name="kiosk-heartbeat"),
    path("", include(router.urls)),
]
