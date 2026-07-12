"""Maintenance forecast — flag assets due for service.

Combines two thresholds: ``Asset.maintenance_interval_hours`` (operating
hours) and ``Asset.maintenance_interval_days`` (calendar days). An asset
is flagged when either threshold has crossed since the last completed
work order, whichever fires first.

Hours-since-last-WO is approximated by ``Asset.hours_used`` because we
do not yet snapshot ``hours_used`` at WO completion. Once the per-WO
snapshot lands (planned for a follow-up phase) the forecast will switch
to a true delta. Until then, an asset whose ``hours_used`` is past
``maintenance_interval_hours`` is flagged regardless of when it was last
serviced — staff should reset ``hours_used`` (or set the interval) after
completing a WO to keep the forecast useful.
"""

from __future__ import annotations

from datetime import datetime
from typing import TypedDict

from django.db.models import Max
from django.utils import timezone

from inventory.models import Asset


class ForecastEntry(TypedDict):
    asset_id: str
    asset_name: str
    category: str | None
    status: str
    hours_used: int
    last_completed_wo_at: str | None
    interval_hours: int | None
    interval_days: int | None
    days_since_last_wo: int | None
    due_reason: str  # "hours" | "days" | "both"


_EXCLUDED_STATUSES = (Asset.Status.RETIRED, Asset.Status.LOST, Asset.Status.DONATED_OUT)


def maintenance_forecast(now: datetime | None = None) -> list[ForecastEntry]:
    """Return assets currently due for maintenance, sorted by reason.

    Sort order: assets due on both axes first, then days-due, then
    hours-due. Within a tier, ordered by name for deterministic output.
    """
    now = now or timezone.now()

    assets = (
        Asset.objects.exclude(status__in=_EXCLUDED_STATUSES)
        .select_related("category")
        .annotate(last_wo_at=Max("maintenance_items__work_orders__completed_at"))
    )

    out: list[ForecastEntry] = []
    for asset in assets:
        if asset.maintenance_interval_hours is None and asset.maintenance_interval_days is None:
            continue

        days_since = None
        if asset.last_wo_at is not None:
            days_since = (now - asset.last_wo_at).days

        hours_due = (
            asset.maintenance_interval_hours is not None
            and asset.hours_used >= asset.maintenance_interval_hours
        )
        days_due = asset.maintenance_interval_days is not None and (
            asset.last_wo_at is None or days_since >= asset.maintenance_interval_days
        )

        if not (hours_due or days_due):
            continue

        if hours_due and days_due:
            reason = "both"
        elif hours_due:
            reason = "hours"
        else:
            reason = "days"

        out.append(
            {
                "asset_id": str(asset.id),
                "asset_name": asset.name,
                "category": asset.category.name if asset.category else None,
                "status": asset.status,
                "hours_used": asset.hours_used,
                "last_completed_wo_at": (
                    asset.last_wo_at.isoformat() if asset.last_wo_at else None
                ),
                "interval_hours": asset.maintenance_interval_hours,
                "interval_days": asset.maintenance_interval_days,
                "days_since_last_wo": days_since,
                "due_reason": reason,
            }
        )

    sort_key = {"both": 0, "days": 1, "hours": 2}
    return sorted(out, key=lambda e: (sort_key[e["due_reason"]], e["asset_name"]))
