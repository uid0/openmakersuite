"""URL routes for the Lockers app (Phase 3 webhook surface)."""

from django.urls import path

from .views import (
    IrBreakEventView,
    LockoutEventView,
    ReedStatusEventView,
    RegistrationAckView,
)

app_name = "lockers"

urlpatterns = [
    path(
        "events/lockout/",
        LockoutEventView.as_view(),
        name="event-lockout",
    ),
    path(
        "events/ir-break/",
        IrBreakEventView.as_view(),
        name="event-ir-break",
    ),
    path(
        "events/reed-status/",
        ReedStatusEventView.as_view(),
        name="event-reed-status",
    ),
    path(
        "registration/ack/",
        RegistrationAckView.as_view(),
        name="registration-ack",
    ),
]
