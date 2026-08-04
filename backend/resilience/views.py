"""Public service-status endpoint.

``GET /api/resilience/status/`` turns circuit-breaker state into something a
member can read: which capabilities are working right now, and what they
lose while one is down. The web app (PR3) uses it to raise a banner and gate
affected controls; ScanTTY (PR4) mirrors it on the kiosk.

Readable by any authenticated user — the whole point is that ordinary
members see the degradation rather than hitting a silent failure. The
payload carries labels and states only: never breaker config, URLs,
credentials, or endpoint hostnames.
"""

from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import ResilienceStatusSerializer
from .services import status_snapshot


class ResilienceStatusView(APIView):
    """``GET /api/resilience/status/`` — current health of external dependencies."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="resilience_status_retrieve",
        responses=ResilienceStatusSerializer,
        summary="Current status of external service dependencies",
        description=(
            "Reports each user-facing capability backed by a circuit breaker "
            "as healthy or degraded. Always returns 200 — a degraded "
            "dependency (or an unreachable breaker store) is reported in the "
            "body, never as an error status."
        ),
    )
    def get(self, request):
        return Response(ResilienceStatusSerializer(status_snapshot()).data)
