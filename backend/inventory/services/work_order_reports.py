"""Asset → work-order traversal shared by the maintenance and asset reports.

Every report that costs or counts maintenance walks *assets*, not work orders,
so each one used to reach the work through ``asset.maintenance_items →
work_orders``. That path only finds **preventive** work: a corrective work
order has no ``MaintenanceItem`` at all and would be silently omitted from the
cost total, the days-in-maintenance span, and the supplies list.

This module is the one place that knows how to reach *all* of an asset's work
orders. Use the two functions as a pair: :func:`prefetch_asset_work_orders`
attaches both halves of the traversal to an ``Asset`` queryset, and
:func:`iter_asset_work_orders` walks them without double-counting the overlap.

It also owns what one of those work orders *cost*: :func:`wo_actual_material_cost`
(real money, the op-768w capture), :func:`wo_estimated_material_cost` (the older
template/material estimate) and :func:`wo_material_cost`, which picks the actual
where it was recorded and falls back to the estimate otherwise. ``tco`` and
``cost_recovery`` price in-house work from these, so both report the same money
for the same job.
"""

from decimal import Decimal
from typing import TYPE_CHECKING, Iterator, Optional

from django.db.models import Prefetch, QuerySet

if TYPE_CHECKING:  # pragma: no cover — typing only
    from inventory.models import MaintenanceItem, WorkOrder

#: Where :func:`prefetch_asset_work_orders` parks the direct-FK half.
DIRECT_WORK_ORDERS_ATTR = "_direct_work_orders"


def prefetch_asset_work_orders(
    asset_queryset: QuerySet,
    work_order_queryset: QuerySet,
) -> QuerySet:
    """Attach both halves of the asset → work-order traversal in one call.

    ``work_order_queryset`` is applied to *both* halves, so any window/status
    filter a report needs holds on preventive and corrective work alike.
    Bundled as one call rather than two exported ``Prefetch`` objects because
    applying only one half is exactly the bug this module exists to prevent.
    """
    return asset_queryset.prefetch_related(
        Prefetch("maintenance_items__work_orders", queryset=work_order_queryset),
        Prefetch(
            "work_orders",
            # ``maintenance_item`` is read for every row yielded from this half;
            # without this it is a query per corrective work order.
            queryset=work_order_queryset.select_related("maintenance_item"),
            to_attr=DIRECT_WORK_ORDERS_ATTR,
        ),
    )


def iter_asset_work_orders(
    asset,
) -> Iterator[tuple["WorkOrder", Optional["MaintenanceItem"]]]:
    """Yield ``(work_order, maintenance_item_or_None)`` for one asset, once each.

    Preventive work orders reach the asset through their template and are
    yielded with it; corrective ones have no template and are yielded with
    ``None``. A preventive work order sits in both halves of the traversal —
    it has a template *and* a direct asset FK — so ids already seen are
    skipped, and a report that sums costs cannot double-count.

    Requires :func:`prefetch_asset_work_orders` on the asset queryset; without
    it the direct half is missing and corrective work would be dropped again,
    so that is an error rather than a quiet under-count.
    """
    direct = getattr(asset, DIRECT_WORK_ORDERS_ATTR, None)
    if direct is None:
        raise AttributeError(
            "iter_asset_work_orders() needs the asset queryset prepared by "
            "prefetch_asset_work_orders(); "
            f"Asset {asset.pk} has no {DIRECT_WORK_ORDERS_ATTR!r}."
        )

    seen: set = set()
    for maintenance_item in asset.maintenance_items.all():
        for work_order in maintenance_item.work_orders.all():
            if work_order.id in seen:
                continue
            seen.add(work_order.id)
            yield work_order, maintenance_item
    for work_order in direct:
        if work_order.id in seen:
            continue
        seen.add(work_order.id)
        yield work_order, work_order.maintenance_item


def wo_estimated_material_cost(
    maintenance_item: Optional["MaintenanceItem"],
    work_order: "WorkOrder",
) -> Decimal:
    """Estimated cost of one completed internal work order.

    A scheduled task (``interval_days`` set) contributes its
    ``MaintenanceItem.estimated_cost``; an unscheduled or one-off task
    contributes the sum of its used materials (``quantity_planned ×
    material.estimated_cost_per_unit``). A corrective work order has no template
    to estimate from (``maintenance_item is None``), so it costs like a one-off.

    This is the pre-op-768w cost model, kept intact: it is what a work order with
    no recorded ``unit_cost`` still reports.
    """
    if maintenance_item is not None and maintenance_item.interval_days is not None:
        return maintenance_item.estimated_cost or Decimal("0.00")
    total = Decimal("0.00")
    for usage in work_order.material_usage.all():
        if usage.was_used and usage.material is not None:
            total += usage.quantity_planned * usage.material.estimated_cost_per_unit
    return total


def wo_actual_material_cost(work_order: "WorkOrder") -> Optional[Decimal]:
    """Real money spent on materials for one work order, or ``None``.

    Sums ``quantity_used × unit_cost`` over the lines that cost the job money —
    the op-768w capture. Returns ``None`` (not zero) when **no** counted line
    carries a ``unit_cost``, which is how a caller tells "this job cost nothing"
    from "this job predates actual-cost capture" and falls back to the estimate.

    Reads ``material_usage.all()``, so a caller who prefetched it pays no extra
    query. Mirrors :attr:`inventory.models.WorkOrder.actual_material_cost` —
    including its op-4pzp rule that an **ad-hoc** line counts as soon as it is
    priced while a template line still has to be marked *used* — and is the same
    sum flattened to ``0.00`` for the API surface. The two must move together:
    a report that disagreed with the work-order screen about what a job cost is
    the bug this mirror exists to avoid.
    """
    total = Decimal("0.00")
    priced = False
    for usage in work_order.material_usage.all():
        if not (usage.was_used or usage.is_ad_hoc):
            continue
        line_cost = usage.actual_cost
        if line_cost is None:
            continue
        priced = True
        total += line_cost
    return total if priced else None


def wo_material_cost(
    maintenance_item: Optional["MaintenanceItem"],
    work_order: "WorkOrder",
) -> tuple[Decimal, bool]:
    """Cost of one internal work order: actual where recorded, estimate otherwise.

    Returns ``(cost, is_actual)``. The choice is made per *work order*, not per
    material line, because a scheduled PM's estimate is a single template-level
    figure that cannot be decomposed and blended line by line.

    A work order that predates actual-cost capture therefore keeps exactly the
    number it reported before, while a priced one reports what was really spent.
    ``cost_recovery`` needs the two figures side by side (it reports the estimate
    in its own column) so it calls the two halves directly; anything that just
    wants "what did this job cost" wants this.
    """
    actual = wo_actual_material_cost(work_order)
    if actual is not None:
        return actual, True
    return wo_estimated_material_cost(maintenance_item, work_order), False
