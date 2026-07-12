"""Computed stock + cost metrics for inventory items (issue-5, op-7wul).

The per-item ``SKU · QOH · QOO · QA · QC · QIT · RP · Lead · Cost`` metrics that
power the web item-detail row and the ScanTTY TUI are computed here so a single
item (the ``/metrics/`` detail action) and a whole page of items (the
``?with_metrics=1`` list annotation) share one implementation.

``compute_item_metrics_batch`` is the workhorse: given an iterable of already
loaded ``InventoryItem`` instances (typically one paginated page) it returns
``{item_id: payload}`` using a BOUNDED number of grouped-aggregate queries —
five, independent of how many items are passed — so the list endpoint never
degrades into an N+1. ``compute_item_metrics`` is the single-item convenience
wrapper the detail action uses.

The ``payload`` dict shape is the pinned contract consumed by
``InventoryMetricsSerializer`` and the ScanTTY worker — do not rename keys.
"""

from django.db.models import ExpressionWrapper, F, IntegerField, Sum

from inventory.models import ItemSupplier, MaintenanceMaterial, WorkOrder
from reorder_queue.models import PurchaseOrder, PurchaseOrderItem

# PO statuses that count as "on order" (QOO): units committed on a live PO that
# has been sent but not fully received, voided, or cancelled.
ON_ORDER_STATUSES = (
    PurchaseOrder.Status.SENT,
    PurchaseOrder.Status.CONFIRMED,
    PurchaseOrder.Status.PARTIALLY_RECEIVED,
)

# Work-order statuses that keep a material "committed" (QC).
OPEN_WO_STATUSES = (
    WorkOrder.Status.OPEN,
    WorkOrder.Status.IN_PROGRESS,
    WorkOrder.Status.BLOCKED,
)


def _cost_trend(current_unit_cost, last_po_unit_cost):
    """Direction of the primary supplier's unit cost vs the most recent PO line."""
    if current_unit_cost is None or last_po_unit_cost is None:
        return "no_history"
    if current_unit_cost > last_po_unit_cost:
        return "up"
    if current_unit_cost < last_po_unit_cost:
        return "down"
    return "flat"


def compute_item_metrics_batch(items):
    """Return ``{item_id: metrics_payload}`` for ``items`` in bounded queries.

    ``items`` is an iterable of already-loaded ``InventoryItem`` instances
    (typically a single paginated page). The number of database queries is
    constant — five grouped aggregates — regardless of how many items are
    passed, so the list endpoint stays O(1) in queries rather than O(n). Every
    item id in ``items`` is present in the result (with zeros / ``None`` where
    there is no PO / work-order / supplier data).
    """
    items = list(items)
    result = {}
    if not items:
        return result

    item_ids = [item.id for item in items]

    # QOO — units on open (non-voided) PO lines, grouped by item. The trailing
    # ``order_by()`` clears ``PurchaseOrderItem``'s Meta ordering, which would
    # otherwise leak into GROUP BY and split an item's total across its POs.
    # (1 query)
    quantity_on_order = {
        row["item_supplier__item"]: row["total"] or 0
        for row in (
            PurchaseOrderItem.objects.filter(
                item_supplier__item_id__in=item_ids,
                is_voided=False,
                purchase_order__status__in=ON_ORDER_STATUSES,
            )
            .values("item_supplier__item")
            .annotate(total=Sum("quantity_ordered"))
            .order_by()
        )
    }

    # QIT — still-pending units on partially-received lines, grouped by item. A
    # subset of QOO (partially_received is an on-order status). (1 query)
    quantity_in_transit = {
        row["item_supplier__item"]: row["total"] or 0
        for row in (
            PurchaseOrderItem.objects.filter(
                item_supplier__item_id__in=item_ids,
                is_voided=False,
                purchase_order__status=PurchaseOrder.Status.PARTIALLY_RECEIVED,
                quantity_received__lt=F("quantity_ordered"),
            )
            .values("item_supplier__item")
            .annotate(
                total=Sum(
                    ExpressionWrapper(
                        F("quantity_ordered") - F("quantity_received"),
                        output_field=IntegerField(),
                    )
                )
            )
            .order_by()  # clear Meta ordering so it can't contaminate GROUP BY
        )
    }

    # QC — quantity committed to open work orders. Select DISTINCT materials
    # first: a task with several open work orders would otherwise multiply the
    # join and double-count the material's quantity. Sum per item in Python so
    # this stays a single query. (1 query)
    quantity_committed = {}
    for _material_id, item_id, quantity in (
        MaintenanceMaterial.objects.filter(
            inventory_item_id__in=item_ids,
            maintenance_item__work_orders__status__in=OPEN_WO_STATUSES,
        )
        .values_list("id", "inventory_item_id", "quantity")
        .order_by()  # clear Meta ordering so DISTINCT dedupes purely by material
        .distinct()
    ):
        quantity_committed[item_id] = quantity_committed.get(item_id, 0.0) + float(quantity or 0)

    # Most-recent PO unit cost per item (drives cost_trend / last_po_unit_cost).
    # Rows arrive newest-first within each item; keep the first one seen. (1 query)
    last_po_unit_cost = {}
    for row in (
        PurchaseOrderItem.objects.filter(item_supplier__item_id__in=item_ids)
        .order_by("item_supplier__item", "-created_at")
        .values("item_supplier__item", "unit_cost_ordered")
    ):
        item_id = row["item_supplier__item"]
        if item_id not in last_po_unit_cost:
            last_po_unit_cost[item_id] = row["unit_cost_ordered"]

    # Primary ItemSupplier per item (unit/package cost, lead time, case size).
    # Mirrors ``InventoryItem.primary_item_supplier`` — is_primary first, then
    # cheapest — but batched so the per-item property never fires. (1 query)
    primary_supplier = {}
    for row in (
        ItemSupplier.objects.filter(item_id__in=item_ids)
        .order_by("item", "-is_primary", "unit_cost")
        .values("item", "unit_cost", "package_cost", "average_lead_time", "quantity_per_package")
    ):
        item_id = row["item"]
        if item_id not in primary_supplier:
            primary_supplier[item_id] = row

    for item in items:
        supplier = primary_supplier.get(item.id)
        unit_cost = supplier["unit_cost"] if supplier else None
        package_cost = supplier["package_cost"] if supplier else None
        lead_time_days = supplier["average_lead_time"] if supplier else None
        case_size = supplier["quantity_per_package"] if supplier else None

        committed = quantity_committed.get(item.id, 0.0)
        # Cost shown on the row: the case cost for case-based items (what you
        # actually pay per package), else the per-unit cost.
        display_cost = package_cost if item.use_case_based_reorder else unit_cost

        result[item.id] = {
            "current_stock": item.current_stock,
            "quantity_on_order": quantity_on_order.get(item.id, 0),
            "quantity_available": float(item.current_stock) - committed,
            "quantity_committed": committed,
            "quantity_in_transit": quantity_in_transit.get(item.id, 0),
            "reorder_point": item.reorder_quantity,
            "lead_time_days": lead_time_days,
            "unit_cost": display_cost,
            "cost_trend": _cost_trend(unit_cost, last_po_unit_cost.get(item.id)),
            "last_po_unit_cost": last_po_unit_cost.get(item.id),
            "is_case_based": item.use_case_based_reorder,
            "case_size": case_size,
        }

    return result


def compute_item_metrics(item):
    """Return the metrics payload for a single item (see the batch variant)."""
    return compute_item_metrics_batch([item])[item.id]
