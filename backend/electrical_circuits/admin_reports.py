"""
Custom admin reports for the electrical topology (oms-b25, AC-1..AC-3).

Three reports are exposed under the existing admin:

* **Circuit Load Report** — every circuit with connected device count,
  estimated max draw, and a colour-coded load percentage.
* **Shared Circuit Audit** — circuits with more than one connected
  asset, sorted by combined criticality.
* **Breaker Trip Impact** — for each breaker, the assets that lose
  power if it trips.

Each view runs at admin-only auth (the ModelAdmin's ``admin_view``
wrapper checks ``request.user.is_staff``) and is engineered to stay
within the AC-6 budget (<2s for 1 site / 10 panels / 200 circuits /
500 devices) by walking outlets and cables in bulk rather than per-row.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Dict, List, Tuple

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.shortcuts import render

from inventory.models import Asset

from .models import (
    Cable,
    PowerBreaker,
    PowerCircuit,
    PowerOutlet,
    PowerPort,
)


def _classify_load(percent: float | None) -> str:
    """Map load percentage to the AC-1 color band.

    ``None`` (no capacity declared) is reported separately so reviewers
    can spot circuits that need a ``max_load_amps`` value before the
    report can grade them.
    """

    if percent is None:
        return "unknown"
    if percent < 60:
        return "green"
    if percent < 80:
        return "yellow"
    return "red"


def _build_outlet_to_assets_map(
    outlet_pks: List[int],
) -> Dict[int, List[Asset]]:
    """Return ``{outlet_pk: [Asset, ...]}`` for the given outlets.

    Bulk-pulls the cables joining outlets to PowerPorts and the assets
    behind those ports in a constant number of queries (cables, ports,
    assets) — the resolver does the same shape internally; we duplicate
    it here so the admin report can attribute devices to specific
    outlets, which the per-circuit resolver does not surface.
    """

    if not outlet_pks:
        return {}

    outlet_ct = ContentType.objects.get_for_model(PowerOutlet)
    port_ct = ContentType.objects.get_for_model(PowerPort)

    str_outlet_ids = [str(pk) for pk in outlet_pks]
    cables = list(
        Cable.objects.filter(cable_type=Cable.CABLE_TYPE_POWER).filter(
            Q(
                endpoint_a_content_type=outlet_ct,
                endpoint_a_object_id__in=str_outlet_ids,
            )
            | Q(
                endpoint_b_content_type=outlet_ct,
                endpoint_b_object_id__in=str_outlet_ids,
            )
        )
    )

    pairs: List[Tuple[int, int]] = []
    for cable in cables:
        if (
            cable.endpoint_a_content_type_id == outlet_ct.id
            and cable.endpoint_b_content_type_id == port_ct.id
        ):
            pairs.append((int(cable.endpoint_a_object_id), int(cable.endpoint_b_object_id)))
        elif (
            cable.endpoint_b_content_type_id == outlet_ct.id
            and cable.endpoint_a_content_type_id == port_ct.id
        ):
            pairs.append((int(cable.endpoint_b_object_id), int(cable.endpoint_a_object_id)))

    if not pairs:
        return {}

    port_to_asset: Dict[int, int] = dict(
        PowerPort.objects.filter(pk__in={p for _, p in pairs}).values_list("pk", "asset_id")
    )
    asset_ids = {port_to_asset[p] for _, p in pairs if p in port_to_asset}
    assets_by_id: Dict[int, Asset] = {a.pk: a for a in Asset.objects.filter(pk__in=asset_ids)}

    out: Dict[int, List[Asset]] = defaultdict(list)
    for outlet_pk, port_pk in pairs:
        asset_id = port_to_asset.get(port_pk)
        if asset_id is None:
            continue
        asset = assets_by_id.get(asset_id)
        if asset is not None:
            out[outlet_pk].append(asset)
    return out


def _circuit_rows(circuits) -> List[dict]:
    """Build one row per circuit with capacity / utilisation / colour band.

    ``circuits`` is expected to come pre-fetched with ``breaker`` and
    ``breaker__panel`` so the iteration stays N+0.
    """

    outlet_qs = PowerOutlet.objects.filter(circuit__in=circuits).values_list(
        "pk", "circuit_id"
    )
    outlets_by_circuit: Dict[int, List[int]] = defaultdict(list)
    for outlet_pk, circuit_id in outlet_qs:
        outlets_by_circuit[circuit_id].append(outlet_pk)

    all_outlet_pks = [pk for pks in outlets_by_circuit.values() for pk in pks]
    outlet_to_assets = _build_outlet_to_assets_map(all_outlet_pks)

    asset_ids = {a.pk for assets in outlet_to_assets.values() for a in assets}
    port_qs = PowerPort.objects.filter(asset_id__in=asset_ids).only(
        "pk", "asset_id", "label", "max_draw_amps"
    )
    primary_port_by_asset: Dict[int, PowerPort] = {}
    for port in port_qs.order_by("asset_id", "label", "pk"):
        primary_port_by_asset.setdefault(port.asset_id, port)

    rows: List[dict] = []
    for circuit in circuits:
        outlet_pks = outlets_by_circuit.get(circuit.pk, [])
        seen_assets: Dict[int, Asset] = {}
        for opk in outlet_pks:
            for a in outlet_to_assets.get(opk, []):
                seen_assets[a.pk] = a

        estimated_draw = Decimal("0")
        for asset in seen_assets.values():
            port = primary_port_by_asset.get(asset.pk)
            if port and port.max_draw_amps is not None:
                estimated_draw += port.max_draw_amps

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
                "connected_devices": list(seen_assets.values()),
                "device_count": len(seen_assets),
                "estimated_draw_amps": float(estimated_draw),
                "capacity_amps": capacity,
                "load_percent": percent_display,
                "color_band": _classify_load(percent),
            }
        )
    return rows


def circuit_load_report_view(model_admin, request):
    """Render the Circuit Load Report (AC-1)."""

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
    """Render the Shared Circuit Audit (AC-2).

    Sort key: ``(critical_count desc, device_count desc, panel/position
    asc)`` so the most consequential pairings rise to the top.
    """

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
        key=lambda r: (-r["critical_count"], -r["device_count"], r["panel"].name, r["breaker"].position)
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
    """Render the Breaker Trip Impact report (AC-3)."""

    breakers = list(
        PowerBreaker.objects.select_related("panel").order_by(
            "panel__name", "position", "pk"
        )
    )
    breaker_pks = [b.pk for b in breakers]

    outlet_qs = PowerOutlet.objects.filter(circuit__breaker_id__in=breaker_pks).values_list(
        "pk", "circuit__breaker_id"
    )
    outlets_by_breaker: Dict[int, List[int]] = defaultdict(list)
    for outlet_pk, breaker_id in outlet_qs:
        outlets_by_breaker[breaker_id].append(outlet_pk)

    all_outlet_pks = [pk for pks in outlets_by_breaker.values() for pk in pks]
    outlet_to_assets = _build_outlet_to_assets_map(all_outlet_pks)

    rows = []
    for breaker in breakers:
        seen: Dict[int, Asset] = {}
        for opk in outlets_by_breaker.get(breaker.pk, []):
            for a in outlet_to_assets.get(opk, []):
                seen[a.pk] = a
        assets = list(seen.values())
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
