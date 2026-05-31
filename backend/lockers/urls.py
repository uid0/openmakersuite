"""URL routes for the Lockers app: webhook receivers + the monitoring API."""

from django.urls import path

from rest_framework.routers import SimpleRouter

from .views import (
    IrBreakEventView,
    LockerViewSet,
    LockoutEventView,
    LockStatusEventView,
    ReedStatusEventView,
    RegistrationAckView,
)

app_name = "lockers"

router = SimpleRouter()
router.register(r"", LockerViewSet, basename="locker")

urlpatterns = [
    path("events/lockout/", LockoutEventView.as_view(), name="event-lockout"),
    path("events/ir-break/", IrBreakEventView.as_view(), name="event-ir-break"),
    path("events/reed-status/", ReedStatusEventView.as_view(), name="event-reed-status"),
    path("events/lock-status/", LockStatusEventView.as_view(), name="event-lock-status"),
    path("registration/ack/", RegistrationAckView.as_view(), name="registration-ack"),
] + router.urls
