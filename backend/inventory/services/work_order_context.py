"""
Shared electrical / LOTO context for work-order parity.

Both the digital work-order detail view (via the API serializer) and the
printable PDF render the same electrical and lockout/tagout (LOTO) data. To
keep them honest — and to make the parity test in
``inventory/tests/test_work_order_parity.py`` straightforward — the data is
built once here and consumed by both surfaces.

Returned shape (deterministic dict ordering — same keys always present so
the digital view shows the empty-state messages required by AC-1 / AC-2):

    {
        "electrical": {
            "rows":        list[(label, value)]   # asset-level CharField stubs
            "outlets":     list[OutletDict]       # joined via asset.location
            "breakers":    list[BreakerDict]      # panels feeding those outlets
            "network_drops": list[NetworkDropDict]
            "is_empty":    bool                    # True when nothing electrical
        },
        "loto": {
            "lockout_type":          str           # human display
            "lockout_type_code":     str           # raw choice value
            "lockout_instructions":  str
            "lockout_responsible":   str
            "is_required":           bool          # True iff lockout_type is a
                                                   # real LOTO/lockout choice
            "is_empty":              bool          # True iff is_required False
        },
    }
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from inventory.models import Asset, WorkOrder


def _build_electrical_rows(asset: "Asset") -> list[list[str]]:
    """Asset-level electrical/lockout summary rows (label + value pairs).

    Mirrors the historical PDF helper — kept here so the digital view shows
    the same labels.
    """
    rows: list[list[str]] = []

    if asset.wiring_type and asset.wiring_type not in (
        asset.WiringType.NONE,
        asset.WiringType.UNKNOWN,
        "",
    ):
        rows.append(["Wiring", asset.get_wiring_type_display()])

    if asset.power_draw_watts:
        rows.append(["Rated Power Draw", f"{asset.power_draw_watts} W"])

    if asset.suite:
        rows.append(["Suite", asset.suite])
    if asset.electrical_box:
        rows.append(["Electrical Box", asset.electrical_box])
    if asset.breaker_location:
        rows.append(["Breaker", asset.breaker_location])
    elif asset.circuit:
        rows.append(["Circuit", asset.circuit])

    if asset.has_interlock:
        interlock_label = asset.get_interlock_type_display() if asset.interlock_type else "Yes"
        rows.append(["Interlock", interlock_label])
        if asset.interlock_responsible:
            rows.append(["Interlock Responsible", asset.interlock_responsible])

    if asset.has_network_drop:
        rows.append(
            [
                "Network Drop",
                asset.network_drop_location or "Yes (location not recorded)",
            ]
        )

    # Site-requirements safety guidance (facilities.AssetSiteRequirements, #880).
    # High-value for a tech about to work on the asset — surfaced on both the
    # digital view and the printed work order.
    if asset.special_requirements:
        rows.append(["Special Requirements", asset.special_requirements])
    if asset.work_safety_notes:
        rows.append(["Crew Should Know", asset.work_safety_notes])

    return rows


def _serialize_outlet(outlet) -> dict[str, Any]:
    breaker = outlet.breaker
    return {
        "id": outlet.id,
        "identifier": outlet.identifier,
        "outlet_type": outlet.outlet_type,
        "outlet_type_display": outlet.get_outlet_type_display(),
        "description": outlet.description,
        "plugged_in_notes": outlet.plugged_in_notes,
        "breaker": (
            {
                "id": breaker.id,
                "panel": breaker.panel,
                "breaker_number": breaker.breaker_number,
                "amperage": breaker.amperage,
                "voltage": breaker.voltage,
                "label": f"{breaker.panel} / {breaker.breaker_number}",
            }
            if breaker
            else None
        ),
    }


def _serialize_breaker(breaker) -> dict[str, Any]:
    return {
        "id": breaker.id,
        "panel": breaker.panel,
        "breaker_number": breaker.breaker_number,
        "amperage": breaker.amperage,
        "voltage": breaker.voltage,
        "poles": breaker.poles,
        "description": breaker.description,
        "label": f"{breaker.panel} / {breaker.breaker_number} ({breaker.amperage}A)",
    }


def _serialize_network_drop(drop) -> dict[str, Any]:
    return {
        "id": drop.id,
        "identifier": drop.identifier,
        "drop_type": drop.drop_type,
        "drop_type_display": drop.get_drop_type_display(),
        "patch_panel": drop.patch_panel,
        "patch_port": drop.patch_port,
        "ip_address": drop.ip_address,
        "description": drop.description,
    }


def _real_lockout(asset: "Asset") -> bool:
    """True iff the asset has a meaningful lockout requirement to display."""
    return bool(
        asset.lockout_type
        and asset.lockout_type not in (asset.LockoutType.NONE, asset.LockoutType.UNKNOWN, "")
    )


def build_loto_context(asset: "Asset") -> dict[str, Any]:
    is_required = _real_lockout(asset)
    return {
        "lockout_type": asset.get_lockout_type_display() if is_required else "",
        "lockout_type_code": asset.lockout_type or "",
        "lockout_instructions": asset.lockout_instructions or "",
        "lockout_responsible": asset.lockout_responsible or "",
        "is_required": is_required,
        "is_empty": not is_required,
    }


def build_electrical_context(asset: "Asset") -> dict[str, Any]:
    """Combine asset CharField stubs with electrical_circuits records.

    The join is location-based: all Outlets / Breakers / NetworkDrops at the
    asset's current location are surfaced. Per the Mayor decision (oms-2da)
    this is the intended path until a direct asset FK is added.
    """
    # electrical_circuits is a separate app; import lazily so this module can
    # be imported during tests that don't include the electrical_circuits app.
    try:
        from electrical_circuits.models import Breaker, NetworkDrop, Outlet
    except ImportError:  # pragma: no cover — electrical_circuits is in INSTALLED_APPS
        Outlet = Breaker = NetworkDrop = None  # type: ignore[assignment]

    outlets: list[dict[str, Any]] = []
    breakers: list[dict[str, Any]] = []
    drops: list[dict[str, Any]] = []

    if asset.location_id and Outlet is not None:
        outlet_qs = Outlet.objects.filter(
            location_id=asset.location_id, is_active=True
        ).select_related("breaker")
        outlets = [_serialize_outlet(o) for o in outlet_qs]

        breaker_ids = [o.breaker_id for o in outlet_qs if o.breaker_id]
        if breaker_ids:
            breaker_qs = Breaker.objects.filter(id__in=breaker_ids, is_active=True)
            breakers = [_serialize_breaker(b) for b in breaker_qs]

        drop_qs = NetworkDrop.objects.filter(location_id=asset.location_id, is_active=True)
        drops = [_serialize_network_drop(d) for d in drop_qs]

    rows = _build_electrical_rows(asset)
    is_empty = not rows and not outlets and not breakers and not drops
    return {
        "rows": rows,
        "outlets": outlets,
        "breakers": breakers,
        "network_drops": drops,
        "is_empty": is_empty,
    }


def build_work_order_context(work_order: "WorkOrder") -> dict[str, Any]:
    """Single source of truth for digital + PDF feature parity."""
    asset = work_order.maintenance_item.asset
    return {
        "electrical": build_electrical_context(asset),
        "loto": build_loto_context(asset),
    }
