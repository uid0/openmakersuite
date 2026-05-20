"""
Custom admin reports for the electrical topology.

Three reports are exposed under the existing admin:

* **Circuit Load Report** — every circuit with connected device count,
  estimated max draw, and a colour-coded load percentage.
* **Shared Circuit Audit** — circuits with more than one connected
  asset, sorted by combined criticality.
* **Breaker Trip Impact** — for each breaker, the assets that lose
  power if it trips.

Each view runs at admin-only auth (the ModelAdmin's ``admin_view``
wrapper checks ``request.user.is_staff``).
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Dict, List

from django.shortcuts import render

from inventory.models import Asset

from .models import PowerBreaker, PowerCircuit


def _classify_load(percent: float | None) -> str:
    """Map load percentage to the color band.

    ``None`` (no capacity declared) is reported separately so reviewers
    can spot circuits that need a ``max_load_amps`` value.
    """

    if percent is None:
        return "unknown"
    if percent < 60:
        return "green"
    if percent < 80:
        return "yellow"
    return "red"


def _assets_by_breaker(breaker_ids) -> Dict[int, List[Asset]]:
    """Return ``{breaker_id: [Asset, ...]}`` for the given breakers."""

    out: Dict[int, List[Asset]] = defaultdict(list)
    for asset in Asset.objects.filter(breaker_id__in=list(breaker_ids)):
        out[asset.breaker_id].append(asset)
    return out


def _circuit_rows(circuits) -> List[dict]:
    """Build one row per circuit with capacity / utilisation / colour band.

    ``circuits`` is expected to come pre-fetched with ``breaker`` and
    ``breaker__panel`` so the iteration stays N+0.
    """

    breaker_ids = {c.breaker_id for c in circuits}
    assets_by_breaker = _assets_by_breaker(breaker_ids)

    rows: List[dict] = []
    for circuit in circuits:
        assets = assets_by_breaker.get(circuit.breaker_id, [])

        estimated_draw = Decimal("0")
        for asset in assets:
            watts = asset.power_draw_watts
            if watts is not None and circuit.breaker.panel and circuit.breaker.panel.voltage:
                # Convert nameplate watts → amps at the panel voltage. The
                # old per-port nameplate is gone; the asset's power_draw_watts
                # is the closest stable equivalent.
                voltage = Decimal(circuit.breaker.panel.voltage)
                if voltage:
                    estimated_draw += Decimal(watts) / voltage

        capacity = circuit.max_load_amps
        if capacity:
            percent = float((estimated_draw / Decimal(capacity)) * Decimal("100"))
            percent_display = round(percent, 1)
        else:
            percent = None
            percent_display = None

        rows.append(
            {
                "circuit": circuit,
                "panel": circuit.breaker.panel,
                "breaker": circuit.breaker,
                "connected_devices": assets,
                "device_count": len(assets),
                "estimated_draw_amps": float(estimated_draw),
                "capacity_amps": capacity,
                "load_percent": percent_display,
                "color_band": _classify_load(percent),
            }
        )
    return rows


def circuit_load_report_view(model_admin, request):
    """Render the Circuit Load Report."""

    circuits = list(
        PowerCircuit.objects.select_related("breaker", "breaker__panel").order_by(
            "breaker__panel__name", "breaker__position", "pk"
        )
    )
    rows = _circuit_rows(circuits)
    context = {
        **model_admin.admin_site.each_context(request),
        "title": "Circuit Load Report",
        "rows": rows,
        "opts": PowerCircuit._meta,
    }
    return render(
        request,
        "admin/electrical_circuits/circuit_load_report.html",
        context,
    )


def shared_circuit_audit_view(model_admin, request):
    """Render the Shared Circuit Audit."""

    circuits = list(
        PowerCircuit.objects.select_related("breaker", "breaker__panel").order_by(
            "breaker__panel__name", "breaker__position", "pk"
        )
    )
    rows = _circuit_rows(circuits)
    shared = [r for r in rows if r["device_count"] >= 2]
    for r in shared:
        r["critical_count"] = sum(
            1 for a in r["connected_devices"] if getattr(a, "is_critical", False)
        )
    shared.sort(
        key=lambda r: (
            -r["critical_count"],
            -r["device_count"],
            r["panel"].name,
            r["breaker"].position,
        )
    )
    context = {
        **model_admin.admin_site.each_context(request),
        "title": "Shared Circuit Audit",
        "rows": shared,
        "opts": PowerCircuit._meta,
    }
    return render(
        request,
        "admin/electrical_circuits/shared_circuit_audit.html",
        context,
    )


def breaker_trip_impact_view(model_admin, request):
    """Render the Breaker Trip Impact report."""

    breakers = list(
        PowerBreaker.objects.select_related("panel").order_by("panel__name", "position", "pk")
    )
    assets_by_breaker = _assets_by_breaker({b.pk for b in breakers})

    rows = []
    for breaker in breakers:
        assets = assets_by_breaker.get(breaker.pk, [])
        critical = [a for a in assets if getattr(a, "is_critical", False)]
        rows.append(
            {
                "breaker": breaker,
                "panel": breaker.panel,
                "assets": assets,
                "critical_loads": critical,
                "asset_count": len(assets),
                "critical_count": len(critical),
            }
        )

    context = {
        **model_admin.admin_site.each_context(request),
        "title": "Breaker Trip Impact",
        "rows": rows,
        "opts": PowerBreaker._meta,
    }
    return render(
        request,
        "admin/electrical_circuits/breaker_trip_impact.html",
        context,
    )


__all__ = [
    "breaker_trip_impact_view",
    "circuit_load_report_view",
    "shared_circuit_audit_view",
]
