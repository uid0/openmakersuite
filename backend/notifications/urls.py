"""
URL configuration for notifications app.
"""

from django.urls import include, path

from config.routers import ApiRouter

from .views import NotificationPreferenceView, NotificationViewSet

router = ApiRouter()
router.register(r"", NotificationViewSet, basename="notification")

urlpatterns = [
    path(
        "preferences/",
        NotificationPreferenceView.as_view(),
        name="notification-preferences",
    ),
    path("", include(router.urls)),
]
