"""Analytics aggregation service.

Pure functions that pull aggregate metrics from the work order, asset, and
maintenance log tables. Used by ``/api/analytics/pulse/`` and (in PR3) by
the monthly Celery email task.

Time-window inputs are half-open: ``[period_start, period_end)``. The
service returns plain dicts so callers can serialize to JSON or render
directly into templates without further shaping.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TypedDict

from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncMonth, TruncQuarter
from django.utils import timezone

from inventory.models import Asset, WorkOrder
from maintenance_orders.models import ThirdPartyWorkOrder

ZERO = Decimal("0.00")
_MONEY = DecimalField(max_digits=12, decimal_places=2)


def _aware_range(period_start: date, period_end: date) -> tuple[datetime, datetime]:
    """Convert dates to aware datetimes in current tz, half-open."""
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(period_start, datetime.min.time()), tz)
    end = timezone.make_aware(datetime.combine(period_end, datetime.min.time()), tz)
    return start, end


def _money_sum(field: str):
    return Coalesce(Sum(field), Value(ZERO, output_field=_MONEY))


class ValueSummary(TypedDict):
    period_start: str
    period_end: str
    internal_completed_count: int
    internal_estimated_external_cost: str
    internal_estimated_internal_cost: str
    internal_net_value: str
    external_closed_count: int
    external_actual_cost: str
    external_estimated_cost: str
    total_value_to_makerspace: str


def value_summary(period_start: date, period_end: date) -> ValueSummary:
    """Return cost-stream totals for completed work in ``[start, end)``.

    Internal stream is the counterfactual: what the in-house WOs *would have
    cost* outside, minus the internal estimated cost. External stream is the
    actual outsourced spend on closed ThirdPartyWorkOrders.
    """
    start_dt, end_dt = _aware_range(period_start, period_end)

    completed_wos = WorkOrder.objects.filter(
        status=WorkOrder.STATUS_COMPLETED,
        completed_at__gte=start_dt,
        completed_at__lt=end_dt,
    )
    internal = completed_wos.aggregate(
        count=Count("id"),
        external_estimated=_money_sum("estimated_external_cost"),
        internal_estimated=_money_sum("maintenance_item__estimated_cost"),
    )

    closed_tpwos = ThirdPartyWorkOrder.objects.filter(
        status=ThirdPartyWorkOrder.STATUS_CLOSED,
        closed_at__gte=start_dt,
        closed_at__lt=end_dt,
    )
    external = closed_tpwos.aggregate(
        count=Count("id"),
        actual=_money_sum("actual_invoice_total"),
        estimated=_money_sum("nte_amount"),
    )

    internal_net = internal["external_estimated"] - internal["internal_estimated"]
    total = internal_net - external["actual"]

    return {
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "internal_completed_count": internal["count"],
        "internal_estimated_external_cost": str(internal["external_estimated"]),
        "internal_estimated_internal_cost": str(internal["internal_estimated"]),
        "internal_net_value": str(internal_net),
        "external_closed_count": external["count"],
        "external_actual_cost": str(external["actual"]),
        "external_estimated_cost": str(external["estimated"]),
        "total_value_to_makerspace": str(total),
    }


def wo_volume(period_start: date, period_end: date, bucket: str = "month") -> list[dict]:
    """Completed WO count per time bucket.

    Returns ``[{'period': 'YYYY-MM-01', 'count': N}, ...]`` sorted by period.
    Bucket must be ``"month"`` or ``"quarter"``.
    """
    if bucket not in ("month", "quarter"):
        raise ValueError(f"bucket must be 'month' or 'quarter', got {bucket!r}")
    start_dt, end_dt = _aware_range(period_start, period_end)
    Trunc = TruncMonth if bucket == "month" else TruncQuarter

    qs = (
        WorkOrder.objects.filter(
            status=WorkOrder.STATUS_COMPLETED,
            completed_at__gte=start_dt,
            completed_at__lt=end_dt,
        )
        .annotate(period=Trunc("completed_at"))
        .values("period")
        .annotate(count=Count("id"))
        .order_by("period")
    )
    return [{"period": row["period"].date().isoformat(), "count": row["count"]} for row in qs]


def top_users(period_start: date, period_end: date, limit: int = 10) -> list[dict]:
    """Top users by completed WO count in window (assigned_to)."""
    start_dt, end_dt = _aware_range(period_start, period_end)
    qs = (
        WorkOrder.objects.filter(
            status=WorkOrder.STATUS_COMPLETED,
            completed_at__gte=start_dt,
            completed_at__lt=end_dt,
        )
        .exclude(assigned_to=None)
        .values(
            "assigned_to_id",
            "assigned_to__username",
            "assigned_to__first_name",
            "assigned_to__last_name",
        )
        .annotate(count=Count("id"))
        .order_by("-count", "assigned_to_id")[:limit]
    )
    out = []
    for row in qs:
        full_name = f"{row['assigned_to__first_name']} {row['assigned_to__last_name']}".strip()
        out.append(
            {
                "user_id": row["assigned_to_id"],
                "username": row["assigned_to__username"],
                "display_name": full_name or row["assigned_to__username"],
                "completed_wo_count": row["count"],
            }
        )
    return out


def utilization(period_start: date, period_end: date) -> list[dict]:
    """Per-asset utilization proxy.

    Returns assets with at least one completed WO in window, including the
    current cumulative ``hours_used`` and the WO count attributed to the
    window. Real per-window hours-delta tracking requires session-level
    events (deferred to a follow-up phase).
    """
    start_dt, end_dt = _aware_range(period_start, period_end)
    qs = (
        Asset.objects.annotate(
            window_wo_count=Count(
                "maintenance_items__work_orders",
                filter=Q(
                    maintenance_items__work_orders__status=WorkOrder.STATUS_COMPLETED,
                    maintenance_items__work_orders__completed_at__gte=start_dt,
                    maintenance_items__work_orders__completed_at__lt=end_dt,
                ),
            )
        )
        .filter(window_wo_count__gt=0)
        .select_related("category")
        .order_by("-window_wo_count", "name")
    )
    return [
        {
            "asset_id": str(a.id),
            "asset_name": a.name,
            "category": a.category.name if a.category else None,
            "hours_used": a.hours_used,
            "completed_wo_count": a.window_wo_count,
            "status": a.status,
        }
        for a in qs
    ]


def category_spend(period_start: date, period_end: date) -> list[dict]:
    """Per-category breakdown of internal estimates and external spend.

    Categories come from ``Asset.category`` reachable via the WO/TPWO asset
    link. Rows where the asset has no category are bucketed under ``None``.
    Sorted by external_actual descending so the biggest spend lands first.
    """
    start_dt, end_dt = _aware_range(period_start, period_end)

    internal_rows = (
        WorkOrder.objects.filter(
            status=WorkOrder.STATUS_COMPLETED,
            completed_at__gte=start_dt,
            completed_at__lt=end_dt,
        )
        .values(
            "maintenance_item__asset__category_id",
            "maintenance_item__asset__category__name",
        )
        .annotate(
            internal_estimated=_money_sum("maintenance_item__estimated_cost"),
            external_estimated=_money_sum("estimated_external_cost"),
            wo_count=Count("id"),
        )
    )

    external_rows = (
        ThirdPartyWorkOrder.objects.filter(
            status=ThirdPartyWorkOrder.STATUS_CLOSED,
            closed_at__gte=start_dt,
            closed_at__lt=end_dt,
        )
        .values("asset__category_id", "asset__category__name")
        .annotate(
            external_actual=_money_sum("actual_invoice_total"),
            tpwo_count=Count("id"),
        )
    )

    by_cat: dict[int | None, dict] = {}

    def _ensure(cid, cname):
        if cid not in by_cat:
            by_cat[cid] = {
                "category_id": cid,
                "category_name": cname,
                "internal_estimated": ZERO,
                "external_estimated": ZERO,
                "external_actual": ZERO,
                "internal_wo_count": 0,
                "external_wo_count": 0,
            }
        return by_cat[cid]

    for row in internal_rows:
        cid = row["maintenance_item__asset__category_id"]
        cname = row["maintenance_item__asset__category__name"]
        entry = _ensure(cid, cname)
        entry["internal_estimated"] = row["internal_estimated"]
        entry["external_estimated"] = row["external_estimated"]
        entry["internal_wo_count"] = row["wo_count"]

    for row in external_rows:
        cid = row["asset__category_id"]
        cname = row["asset__category__name"]
        entry = _ensure(cid, cname)
        entry["external_actual"] = row["external_actual"]
        entry["external_wo_count"] = row["tpwo_count"]

    return [
        {
            **entry,
            "internal_estimated": str(entry["internal_estimated"]),
            "external_estimated": str(entry["external_estimated"]),
            "external_actual": str(entry["external_actual"]),
        }
        for entry in sorted(
            by_cat.values(),
            key=lambda r: (-float(r["external_actual"]), r["category_name"] or ""),
        )
    ]
