"""DRF viewsets for electrical circuits and network drops."""

from django.http import HttpResponse

from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Breaker, LightSwitch, NetworkDrop, Outlet
from .serializers import (
    BreakerSerializer,
    LightSwitchSerializer,
    NetworkDropSerializer,
    OutletSerializer,
)
from .utils.panel_directory_pdf import generate_network_drop_list_pdf, generate_panel_directory_pdf


class _LocationFilterMixin:
    """Allow ?location=<id> and ?is_active=<bool> filtering on list endpoints."""

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        location_id = params.get("location")
        if location_id:
            qs = qs.filter(location_id=location_id)
        is_active = params.get("is_active")
        if is_active is not None:
            truthy = is_active.lower() in ("1", "true", "yes")
            qs = qs.filter(is_active=truthy)
        return qs


class BreakerViewSet(_LocationFilterMixin, viewsets.ModelViewSet):
    queryset = Breaker.objects.select_related("location").all()
    serializer_class = BreakerSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        panel = self.request.query_params.get("panel")
        if panel:
            qs = qs.filter(panel=panel)
        return qs


class OutletViewSet(_LocationFilterMixin, viewsets.ModelViewSet):
    queryset = Outlet.objects.select_related("location", "breaker").all()
    serializer_class = OutletSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        breaker_id = self.request.query_params.get("breaker")
        if breaker_id:
            qs = qs.filter(breaker_id=breaker_id)
        return qs


class LightSwitchViewSet(_LocationFilterMixin, viewsets.ModelViewSet):
    queryset = LightSwitch.objects.select_related("location", "breaker", "controls_location").all()
    serializer_class = LightSwitchSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        controls_location = self.request.query_params.get("controls_location")
        if controls_location:
            qs = qs.filter(controls_location_id=controls_location)
        return qs


class NetworkDropViewSet(_LocationFilterMixin, viewsets.ModelViewSet):
    queryset = NetworkDrop.objects.select_related("location").all()
    serializer_class = NetworkDropSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        drop_type = self.request.query_params.get("drop_type")
        if drop_type:
            qs = qs.filter(drop_type=drop_type)
        return qs


class PanelDirectoryReportView(APIView):
    """
    GET /api/electrical-circuits/reports/panel-directory.pdf?panel=<name>

    Generates a printable directory of breakers and the outlets / light
    switches each one feeds. Omitting ``panel`` produces a report that
    covers every panel in the system.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        panel = request.query_params.get("panel") or None
        pdf_bytes = generate_panel_directory_pdf(panel=panel)
        slug = panel.replace(" ", "-") if panel else "all"
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="panel-directory-{slug}.pdf"'
        return response


class NetworkDropListReportView(APIView):
    """
    GET /api/electrical-circuits/reports/network-drop-list.pdf?location=<id>

    Lists network drops, grouped by location, with patch-panel/port and
    connected-device notes. Omitting ``location`` returns every drop.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        location_param = request.query_params.get("location")
        location_id = None
        if location_param:
            try:
                location_id = int(location_param)
            except ValueError:
                return Response(
                    {"detail": "location must be an integer id."},
                    status=400,
                )
        pdf_bytes = generate_network_drop_list_pdf(location_id=location_id)
        slug = str(location_id) if location_id is not None else "all"
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="network-drop-list-{slug}.pdf"'
        return response
