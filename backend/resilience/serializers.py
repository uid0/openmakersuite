"""Response serializers for the public service-status endpoint.

Read-only: these render the plain dicts built by
:mod:`resilience.services`. They exist so drf-spectacular publishes the
exact response shape the frontend (PR3) and ScanTTY (PR4) consume, and so
timestamps serialize in DRF's canonical ``...Z`` form.
"""

from __future__ import annotations

from rest_framework import serializers


class ServiceStatusSerializer(serializers.Serializer):
    """One user-facing capability and whether it currently works."""

    key = serializers.CharField(help_text="Stable machine key, e.g. 'device_control'.")
    label = serializers.CharField(help_text="User-facing service name.")
    description = serializers.CharField(help_text="What the user loses while degraded.")
    state = serializers.ChoiceField(
        choices=["closed", "half_open", "open"],
        help_text="Aggregate breaker state: closed (working), half_open (on trial), open (down).",
    )
    healthy = serializers.BooleanField(help_text="True when state is 'closed'.")
    since = serializers.DateTimeField(
        allow_null=True,
        help_text="When the service entered its current state; null if it never transitioned.",
    )
    last_error = serializers.CharField(
        allow_null=True,
        help_text="Error detail recorded on that transition; null when there is none.",
    )
    degraded_count = serializers.IntegerField(
        help_text="Member breakers currently open or half-open."
    )
    total_count = serializers.IntegerField(
        help_text="Member breakers with known state (1 for single-breaker services)."
    )


class ResilienceStatusSerializer(serializers.Serializer):
    """Payload of ``GET /api/resilience/status/``."""

    degraded = serializers.BooleanField(help_text="True when any service is not healthy.")
    checked_at = serializers.DateTimeField(help_text="When this snapshot was taken.")
    services = ServiceStatusSerializer(many=True)
