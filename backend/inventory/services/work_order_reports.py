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
"""

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
