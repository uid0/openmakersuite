"""DRF viewsets and views for the LOTO data model (oms-78j)."""

from __future__ import annotations

from django.shortcuts import get_object_or_404

from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from inventory.models import Asset
from inventory.services.work_order_context import build_loto_context

from .models import AssetEnergySource, LOTODevice
from .serializers import (
    AssetEnergySourceSerializer,
    AssetLOTORequirementsSerializer,
    LOTODeviceSerializer,
)


class LOTODeviceViewSet(viewsets.ModelViewSet):
    """
    CRUD for the physical LOTO device inventory.

    Filters: ?status, ?device_type, ?location, ?assigned_to.
    """

    queryset = LOTODevice.objects.select_related("location", "assigned_to").all()
    serializer_class = LOTODeviceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        for field in ("status", "device_type"):
            value = params.get(field)
            if value:
                qs = qs.filter(**{field: value})
        for field in ("location", "assigned_to"):
            value = params.get(field)
            if value:
                qs = qs.filter(**{f"{field}_id": value})
        return qs


class AssetEnergySourceViewSet(viewsets.ModelViewSet):
    """
    CRUD for per-asset energy sources.

    Filter by ?asset=<asset_id> to scope to one asset.
    """

    queryset = (
        AssetEnergySource.objects.select_related("asset").prefetch_related("required_devices").all()
    )
    serializer_class = AssetEnergySourceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        asset_id = self.request.query_params.get("asset")
        if asset_id:
            qs = qs.filter(asset_id=asset_id)
        source_type = self.request.query_params.get("source_type")
        if source_type:
            qs = qs.filter(source_type=source_type)
        return qs


class AssetLOTORequirementsView(APIView):
    """
    GET /api/loto/assets/<asset_id>/loto-requirements/

    Returns the structured LOTO info for an asset: the legacy lockout_* text
    fields plus all configured energy sources with their required devices.
    Consumed by work-order surfaces (oms-2da AC-2 join) and the asset detail
    page.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, asset_id):
        asset = get_object_or_404(
            Asset.objects.prefetch_related(
                "energy_sources__required_devices",
            ),
            pk=asset_id,
        )
        loto_ctx = build_loto_context(asset)
        payload = {
            "asset_id": asset.id,
            "asset_name": asset.name,
            "lockout_type": loto_ctx["lockout_type_code"],
            "lockout_type_display": loto_ctx["lockout_type"],
            "lockout_instructions": loto_ctx["lockout_instructions"],
            "lockout_responsible": loto_ctx["lockout_responsible"],
            "is_required": loto_ctx["is_required"] or asset.energy_sources.exists(),
            "energy_sources": list(asset.energy_sources.all()),
        }
        serializer = AssetLOTORequirementsSerializer(payload)
        return Response(serializer.data)
