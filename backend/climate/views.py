"""DRF views for climate models."""

from __future__ import annotations

from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from .models import Thermostat
from .serializers import ThermostatSerializer


class ThermostatViewSet(viewsets.ModelViewSet):
    """CRUD for Thermostat records."""

    queryset = Thermostat.objects.select_related(
        "location",
        "controls_location",
        "controlled_asset",
    ).all()
    serializer_class = ThermostatSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        location = self.request.query_params.get("location")
        controls_location = self.request.query_params.get("controls_location")
        if location:
            qs = qs.filter(location_id=location)
        if controls_location:
            qs = qs.filter(controls_location_id=controls_location)
        return qs
