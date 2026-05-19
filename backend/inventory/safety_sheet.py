"""Per-Location safety-sheet endpoint (oms-gzycmj).

Returns a single payload structured for the printable Safety Sign:

* lights — every ``LightSwitch`` whose ``controls_location == this_location``,
  with its (legacy) breaker.
* outlets — every legacy ``Outlet`` plus every ``PowerOutlet`` in this
  location, grouped by breaker so multiple receptacles on the same circuit
  collapse to one row.
* thermostats — every ``Thermostat`` whose ``controls_location == this_location``
  with the breaker(s) that kill its ``controlled_asset``.
* all_kill_breakers — deduped union for the "trip these to de-energize the
  room" emergency footer.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Iterable, List, Tuple

from django.shortcuts import get_object_or_404

from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


class IsStaffUser(BasePermission):
    """Staff-only gate (mirrors electrical_circuits.safety_views.IsStaffUser)."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_staff)


def _legacy_breaker_dict(breaker) -> dict | None:
    if breaker is None:
        return None
    return {
        "panel": breaker.panel,
        "position": breaker.breaker_number,
        "amperage": breaker.amperage,
        "breaker_id": breaker.pk,
        "source": "legacy",
    }


def _power_breaker_dict(power_breaker) -> dict:
    return {
        "panel": power_breaker.panel.name,
        "position": power_breaker.position,
        "amperage": power_breaker.amperage,
        "breaker_id": power_breaker.pk,
        "source": "power",
    }


def _key(b: dict) -> Tuple[str, str, str]:
    """Stable identity for dedup across legacy + power breakers.

    ``source`` is included so a legacy breaker and a freshly-migrated power
    breaker with overlapping (panel, position) text still collapse cleanly
    when they actually represent the same physical breaker after migration.
    For pre-migration data the two sources name different rows, but the
    operator-facing identity ``(panel, position)`` still dedupes correctly.
    """

    return (str(b["panel"]), str(b["position"]), b.get("amperage_key", ""))


def _dedup_sort(breakers: Iterable[dict]) -> List[dict]:
    """Stable dedup by (panel, position); preserves first-seen amperage."""

    seen: "OrderedDict[Tuple[str, str], dict]" = OrderedDict()
    for b in breakers:
        if b is None:
            continue
        key = (str(b["panel"]), str(b["position"]))
        if key not in seen:
            seen[key] = {
                "panel": b["panel"],
                "position": b["position"],
                "amperage": b.get("amperage"),
            }
    return sorted(seen.values(), key=lambda r: (str(r["panel"]), str(r["position"])))


def _build_lights(location) -> List[dict]:
    from electrical_circuits.models import LightSwitch

    switches = (
        LightSwitch.objects.filter(controls_location=location, is_active=True)
        .select_related("breaker")
        .order_by("identifier")
    )
    rows: List[dict] = []
    for s in switches:
        b = s.breaker
        rows.append(
            {
                "switch_id": s.pk,
                "switch_label": s.identifier,
                "panel": b.panel if b else None,
                "position": b.breaker_number if b else None,
                "amperage": b.amperage if b else None,
                "breaker_id": b.pk if b else None,
            }
        )
    return rows


def _build_outlets(location) -> List[dict]:
    """Group legacy + power outlets in ``location`` by their breaker."""

    from electrical_circuits.models import Outlet, PowerOutlet

    groups: "OrderedDict[Tuple[str, str, str], dict]" = OrderedDict()

    legacy_outlets = (
        Outlet.objects.filter(location=location, is_active=True)
        .select_related("breaker")
        .order_by("identifier")
    )
    for o in legacy_outlets:
        if o.breaker is None:
            key = ("__no_breaker__", "", "legacy")
            label_pos = None
            panel = None
            amperage = None
            breaker_id = None
            source = "legacy"
        else:
            key = (str(o.breaker.panel), str(o.breaker.breaker_number), "legacy")
            label_pos = o.breaker.breaker_number
            panel = o.breaker.panel
            amperage = o.breaker.amperage
            breaker_id = o.breaker.pk
            source = "legacy"
        groups.setdefault(
            key,
            {
                "panel": panel,
                "position": label_pos,
                "amperage": amperage,
                "breaker_id": breaker_id,
                "source": source,
                "outlet_count": 0,
                "outlets": [],
            },
        )
        groups[key]["outlet_count"] += 1
        groups[key]["outlets"].append(
            {"id": o.pk, "identifier": o.identifier, "outlet_type": o.outlet_type}
        )

    power_outlets = (
        PowerOutlet.objects.filter(location=location)
        .exclude(status=PowerOutlet.STATUS_CAPPED)
        .select_related("circuit", "circuit__breaker", "circuit__breaker__panel")
        .order_by("label", "pk")
    )
    for po in power_outlets:
        breaker = po.circuit.breaker if po.circuit_id else None
        if breaker is None:
            key = ("__no_breaker__", "", "power")
            panel = None
            position = None
            amperage = None
            breaker_id = None
        else:
            key = (breaker.panel.name, breaker.position, "power")
            panel = breaker.panel.name
            position = breaker.position
            amperage = breaker.amperage
            breaker_id = breaker.pk
        groups.setdefault(
            key,
            {
                "panel": panel,
                "position": position,
                "amperage": amperage,
                "breaker_id": breaker_id,
                "source": "power",
                "outlet_count": 0,
                "outlets": [],
            },
        )
        groups[key]["outlet_count"] += 1
        groups[key]["outlets"].append(
            {"id": po.pk, "identifier": po.label or f"#{po.pk}", "outlet_type": po.outlet_type}
        )

    return sorted(
        groups.values(),
        key=lambda g: (str(g.get("panel") or "~"), str(g.get("position") or "")),
    )


def _build_thermostats(location) -> List[dict]:
    """Return thermostats and the breakers that kill their controlled assets."""

    from climate.models import Thermostat
    from climate.services.kill_breakers import resolve_kill_breakers

    thermostats = (
        Thermostat.objects.filter(controls_location=location)
        .select_related("controlled_asset", "location")
        .order_by("label")
    )
    rows: List[dict] = []
    for t in thermostats:
        kill_breakers = resolve_kill_breakers(t.controlled_asset)
        needs_review = bool(t.needs_review) or (t.controlled_asset_id is None) or not kill_breakers
        controlled = None
        if t.controlled_asset_id:
            controlled = {
                "id": str(t.controlled_asset.pk),
                "name": t.controlled_asset.name,
            }
        rows.append(
            {
                "id": t.pk,
                "label": t.label,
                "manufacturer": t.manufacturer,
                "model": t.model,
                "controlled_asset": controlled,
                "kill_breakers": kill_breakers,
                "needs_review": needs_review,
            }
        )
    return rows


def build_safety_sheet(location) -> dict:
    """Pure builder so tests can target it directly."""

    lights = _build_lights(location)
    outlets = _build_outlets(location)
    thermostats = _build_thermostats(location)

    all_breakers: List[dict] = []
    for row in lights:
        if row.get("panel") and row.get("position") is not None:
            all_breakers.append(
                {
                    "panel": row["panel"],
                    "position": row["position"],
                    "amperage": row.get("amperage"),
                }
            )
    for group in outlets:
        if group.get("panel") and group.get("position") is not None:
            all_breakers.append(
                {
                    "panel": group["panel"],
                    "position": group["position"],
                    "amperage": group.get("amperage"),
                }
            )
    for t in thermostats:
        for kb in t["kill_breakers"]:
            all_breakers.append(
                {
                    "panel": kb["panel"],
                    "position": kb["position"],
                    "amperage": kb.get("amperage"),
                }
            )

    return {
        "location": {"id": location.pk, "name": location.name},
        "lights": lights,
        "outlets": outlets,
        "thermostats": thermostats,
        "all_kill_breakers": _dedup_sort(all_breakers),
    }


class LocationSafetySheetView(APIView):
    """``GET /api/inventory/locations/<id>/safety-sheet/`` (oms-gzycmj).

    Staff-gated read-only endpoint that powers the printable Safety Sign.
    """

    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request, location_id: int):
        from .models import Location

        location = get_object_or_404(Location, pk=location_id)
        return Response(build_safety_sheet(location))
