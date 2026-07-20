"""Read helpers for the stored demand forecast.

The nightly forecasting task writes one
:class:`~inventory.models.DemandForecast` row per item per run. This module
reads them back: it resolves the **latest** row per item and applies the report
filters/ordering used by the ``demand_forecast`` and ``reorder_alerts`` actions
on :class:`inventory.views.InventoryReportViewSet`.

It queries stored rows only -- no forecasting maths live here. Until the task
populates the table every helper returns ``[]``; that empty state is part of the
contract (and is tested). The urgency ordering mirrors
:func:`inventory.services.component_forecast.build_component_forecast`
(most-urgent first).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import OuterRef, Subquery

from inventory.models import DemandForecast

if TYPE_CHECKING:  # pragma: no cover
    from django.db.models import QuerySet


def _urgency_key(forecast: DemandForecast) -> tuple:
    """Sort key: reorder-flagged first, then soonest due (nulls last).

    ``days_until_due`` is NULL for items without enough purchase history to
    infer a cadence; they sort last within their flag group rather than
    pretending to be due now.
    """
    return (
        not forecast.needs_reorder,
        forecast.days_until_due is None,
        forecast.days_until_due if forecast.days_until_due is not None else 0.0,
    )


def _latest_per_item_qs() -> "QuerySet[DemandForecast]":
    """Latest forecast row per **active, non-retired, non-serialized** item.

    Uses a correlated subquery (``pk`` of the newest row for the same item) so
    it works identically on SQLite and PostgreSQL -- ``DISTINCT ON`` is
    Postgres-only. ``select_related`` keeps the serializer's item/category
    lookups off the per-row path.
    """
    newest_pk = (
        DemandForecast.objects.filter(item=OuterRef("item"))
        .order_by("-generated_at")
        .values("pk")[:1]
    )
    return (
        DemandForecast.objects.filter(pk=Subquery(newest_pk))
        .filter(
            item__is_active=True,
            item__is_retired=False,
            item__is_serialized=False,
        )
        .select_related("item", "item__category")
    )


def latest_demand_forecasts(*, low_stock_only: bool = False) -> list[DemandForecast]:
    """Latest forecast per eligible item, most-urgent first.

    Args:
        low_stock_only: When ``True``, keep only rows whose ``needs_reorder`` is
            set (due to be bought within the supplier lead time).

    Returns:
        A list of :class:`~inventory.models.DemandForecast` rows (possibly
        empty) sorted reorder-flagged-first then soonest-due-first.
    """
    qs = _latest_per_item_qs()
    if low_stock_only:
        qs = qs.filter(needs_reorder=True)
    return sorted(qs, key=_urgency_key)


def reorder_alert_forecasts() -> list[DemandForecast]:
    """The notify set: latest rows for opted-in items that are due to reorder.

    Filters the latest-per-item rows to ``item.reorder_alerts_enabled=True`` AND
    ``needs_reorder=True`` -- the items a user asked to be pinged about that are
    actually due to be bought again.
    """
    qs = _latest_per_item_qs().filter(
        item__reorder_alerts_enabled=True,
        needs_reorder=True,
    )
    return sorted(qs, key=_urgency_key)
