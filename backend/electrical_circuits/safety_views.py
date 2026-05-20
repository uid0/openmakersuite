"""
Safety query API endpoints (oms-b25, AC-1..AC-5).

Read-only views that surface the resolvers in
:mod:`electrical_circuits.services.power_chain` over HTTP. Used by the
admin UI and the upcoming frontend visualization ([6/7]) to answer the
"what loses power if I trip this?" / "what feeds this asset?" questions
before maintenance touches a panel.

All endpoints require ``request.user.is_staff`` (AC-5). Anonymous and
non-staff authenticated users get 401/403 respectively.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models
from django.shortcuts import get_object_or_404

from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from inventory.models import Asset

from .models import PowerBreaker, PowerCircuit, PowerOutlet, PowerPanel
from .services.power_chain import (
    assets_by_outlet,
    get_devices_on_circuit,
    get_power_chain,
    get_trip_impact,
)


class IsStaffUser(BasePermission):
    """Staff-only gate for safety query endpoints (AC-5)."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_staff)


def _serialize_asset(asset: Asset) -> dict:
    return {
        "id": asset.pk,
        "name": asset.name,
        "asset_tag": asset.asset_tag,
        "is_critical": bool(asset.is_critical),
    }


def _serialize_breaker(breaker: PowerBreaker) -> dict:
    return {
        "id": breaker.pk,
        "panel_id": breaker.panel_id,
        "panel_name": breaker.panel.name,
        "position": breaker.position,
        "amperage": breaker.amperage,
        "phase": breaker.phase,
        "pole_count": breaker.pole_count,
        "status": breaker.status,
        "review_status": breaker.review_status,
        "review_note": breaker.review_note,
        "label": breaker.label,
    }


def _serialize_circuit(circuit: PowerCircuit) -> dict:
    return {
        "id": circuit.pk,
        "label": circuit.label,
        "breaker_id": circuit.breaker_id,
        "max_load_amps": circuit.max_load_amps,
        "conductor_size": circuit.conductor_size,
    }


def _serialize_outlet(outlet, connected_assets=None) -> dict:
    return {
        "id": outlet.pk,
        "label": outlet.label,
        "outlet_type": outlet.outlet_type,
        "status": outlet.status,
        "location_id": outlet.location_id,
        "location_name": outlet.location.name if outlet.location_id else None,
        "connected_assets": [_serialize_asset(a) for a in (connected_assets or [])],
    }


def _estimate_max_draw_amps(devices, voltage: int | None) -> Decimal:
    """Sum nameplate watts across ``devices``, converted to amps at ``voltage``.

    Each asset's ``power_draw_watts`` is the nameplate rating. We divide by
    the panel voltage (when known) to get amps; assets with no declared
    draw or panels with no voltage contribute 0 — we surface those assets
    in ``connected_devices`` rather than guessing a load.
    """

    if not voltage:
        return Decimal("0")
    total = Decimal("0")
    voltage_dec = Decimal(voltage)
    for asset in devices:
        if asset.power_draw_watts is not None:
            total += Decimal(asset.power_draw_watts) / voltage_dec
    return total


class PowerBreakerTripImpactView(APIView):
    """``GET /api/electrical/breakers/<id>/trip-impact/`` (AC-1)."""

    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request, pk: int):
        breaker = get_object_or_404(
            PowerBreaker.objects.select_related("panel"),
            pk=pk,
        )
        impact = get_trip_impact(breaker)
        return Response(
            {
                "breaker": _serialize_breaker(breaker),
                "assets": [_serialize_asset(a) for a in impact["assets"]],
                "critical_loads": [_serialize_asset(a) for a in impact["critical_loads"]],
            }
        )


class PowerCircuitLoadView(APIView):
    """``GET /api/electrical/circuits/<id>/load/`` (AC-2)."""

    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request, pk: int):
        circuit = get_object_or_404(
            PowerCircuit.objects.select_related("breaker", "breaker__panel"),
            pk=pk,
        )
        devices = list(get_devices_on_circuit(circuit))
        panel_voltage = (
            circuit.breaker.panel.voltage if circuit.breaker and circuit.breaker.panel else None
        )
        estimated_draw = _estimate_max_draw_amps(devices, panel_voltage)

        capacity = circuit.max_load_amps
        if capacity:
            utilization = float((estimated_draw / Decimal(capacity)) * Decimal("100"))
        else:
            utilization = None

        return Response(
            {
                "circuit": _serialize_circuit(circuit),
                "connected_device_count": len(devices),
                "connected_devices": [_serialize_asset(a) for a in devices],
                "estimated_max_draw_amps": float(estimated_draw),
                "capacity_amps": capacity,
                "capacity_utilization_percent": (
                    round(utilization, 2) if utilization is not None else None
                ),
            }
        )


class PowerPanelTopologyView(APIView):
    """``GET /api/electrical/panels/<id>/topology/`` (AC-3).

    Returns the full panel → breaker → circuit → outlet tree as one
    nested document. The view pre-fetches the entire subtree in three
    queries so a 200-circuit panel renders without N+1 fanout (AC-6).
    """

    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request, pk: int):
        panel = get_object_or_404(
            PowerPanel.objects.select_related(
                "location",
                "fed_by",
                "fed_by__breaker",
                "fed_by__breaker__panel",
            ),
            pk=pk,
        )
        breakers = list(
            PowerBreaker.objects.filter(panel=panel)
            .order_by("position", "pk")
            .prefetch_related(
                "circuits",
                "circuits__outlets",
                "circuits__outlets__location",
            )
        )

        # Resolve {outlet_pk: [Asset]} once for every outlet on the panel
        # so per-outlet serialization stays O(1).
        outlet_pks = list(
            PowerOutlet.objects.filter(circuit__breaker__panel=panel).values_list("pk", flat=True)
        )
        outlet_assets = assets_by_outlet(outlet_pks)

        breakers_payload = []
        for breaker in breakers:
            circuits_payload = []
            for circuit in breaker.circuits.all():
                outlets_payload = [
                    _serialize_outlet(o, connected_assets=outlet_assets.get(o.pk, []))
                    for o in circuit.outlets.all()
                ]
                circuits_payload.append(
                    {
                        **_serialize_circuit(circuit),
                        "outlets": outlets_payload,
                    }
                )
            breakers_payload.append(
                {
                    **_serialize_breaker(breaker),
                    "circuits": circuits_payload,
                }
            )

        fed_by_summary = None
        if panel.fed_by_id is not None:
            feeder = panel.fed_by
            fb_breaker = feeder.breaker
            fb_panel = fb_breaker.panel
            fed_by_summary = {
                "circuit_id": feeder.id,
                "circuit_label": feeder.label or "",
                "breaker_id": fb_breaker.id,
                "breaker_position": fb_breaker.position,
                "breaker_amperage": fb_breaker.amperage,
                "breaker_pole_count": fb_breaker.pole_count,
                "panel_id": fb_panel.id,
                "panel_name": fb_panel.name,
            }

        # Reverse: list panels fed by THIS panel's circuits, so the UI
        # can offer "Sub-panels: LV4, LV5" navigation in one round-trip.
        downstream = list(
            PowerPanel.objects.filter(fed_by__breaker__panel=panel)
            .order_by("name")
            .values("id", "name")
        )

        return Response(
            {
                "id": panel.pk,
                "name": panel.name,
                "location_id": panel.location_id,
                "location_name": panel.location.name,
                "phase_configuration": panel.phase_configuration,
                "voltage": panel.voltage,
                "main_breaker_amperage": panel.main_breaker_amperage,
                "breaker_type": panel.breaker_type,
                "numbering_direction": panel.numbering_direction,
                "fed_by_summary": fed_by_summary,
                "downstream_panels": downstream,
                "breakers": breakers_payload,
            }
        )


class AssetPowerChainView(APIView):
    """``GET /api/assets/<id>/power-chain/`` (AC-4)."""

    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request, pk: int):
        asset = get_object_or_404(Asset, pk=pk)
        chain = get_power_chain(asset)
        chain_payload = []
        for hop in chain:
            entry = {"kind": hop.kind, "id": hop.obj.pk, "type": type(hop.obj).__name__}
            obj = hop.obj
            label = (
                getattr(obj, "name", None)
                or getattr(obj, "label", None)
                or getattr(obj, "position", None)
                or str(obj.pk)
            )
            entry["label"] = label
            chain_payload.append(entry)

        return Response(
            {
                "asset": _serialize_asset(asset),
                "chain": chain_payload,
            }
        )


class PowerPanelListView(APIView):
    """``GET /api/electrical/panels/`` — directory of every panel.

    Backs the frontend visualization ([6/7]). Annotates each panel with
    its location, breaker count, and the ``needs_review`` flag set on
    migration-created placeholders so admins can prioritize cleanup.
    """

    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        panels = (
            PowerPanel.objects.select_related("location")
            .annotate(breaker_count=models.Count("breakers"))
            .order_by("location__name", "name")
        )
        return Response(
            {
                "results": [
                    {
                        "id": panel.pk,
                        "name": panel.name,
                        "location_id": panel.location_id,
                        "location_name": panel.location.name,
                        "phase_configuration": panel.phase_configuration,
                        "voltage": panel.voltage,
                        "main_breaker_amperage": panel.main_breaker_amperage,
                        "breaker_count": panel.breaker_count,
                        "needs_review": panel.needs_review,
                    }
                    for panel in panels
                ]
            }
        )
