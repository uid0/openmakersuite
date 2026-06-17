"""Device-management API: list/forget known devices + 'this wasn't me' revoke.

FP3 (oms-ltqs3). Owner-scoped — a user only ever sees and manages their own
:class:`~notifications.models.KnownDevice` rows. Mounted at ``/api/account/``.
"""

from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .account_security import record_device_forgotten, revoke_all_sessions_and_tokens
from .device_login import _read_device_token, get_client_ip
from .models import KnownDevice
from .serializers import KnownDeviceSerializer


class KnownDeviceViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """List, inspect, and forget the current user's known devices, plus the
    revoke-all ("this wasn't me") security action.

    Devices are created by the login hook (FP1), never via this API, so there
    is no create/update — only list, retrieve, destroy (forget), and the
    ``revoke-all`` action. Authentication uses the project defaults (JWT +
    session) so the endpoint is reachable from the SPA and the browsable API.
    """

    serializer_class = KnownDeviceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Owner-scoped: never expose (or let a user forget) another's devices.
        return KnownDevice.objects.filter(user=self.request.user)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        # Lets the serializer flag which row is the requesting browser.
        context["current_device_token"] = _read_device_token(self.request)
        return context

    def perform_destroy(self, instance):
        # "Forget this device": audit before the row disappears.
        record_device_forgotten(
            instance,
            actor=self.request.user,
            ip_address=get_client_ip(self.request),
            user_agent=self.request.headers.get("user-agent", ""),
        )
        instance.delete()

    @action(detail=False, methods=["post"], url_path="revoke-all")
    def revoke_all(self, request):
        """ "This wasn't me": invalidate every session and JWT for the user."""
        result = revoke_all_sessions_and_tokens(
            request.user,
            actor=request.user,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent", ""),
        )
        return Response(
            {
                "detail": "All sessions and tokens have been revoked. Please sign in again.",
                "sessions_deleted": result["sessions_deleted"],
            },
            status=status.HTTP_200_OK,
        )
