"""Computed stock + cost metrics for inventory items (issue-5, op-7wul).

The per-item ``SKU · QOH · QOO · QA · QC · QIT · RP · Lead · Cost`` metrics that
power the web item-detail row and the ScanTTY TUI are computed here so a single
item (the ``/metrics/`` detail action) and a whole page of items (the
``?with_metrics=1`` list annotation) share one implementation.

``compute_item_metrics_batch`` is the workhorse: given an iterable of already
loaded ``InventoryItem`` instances (typically one paginated page) it returns
``{item_id: payload}`` using a BOUNDED number of grouped-aggregate queries —
six, independent of how many items are passed — so the list endpoint never
degrades into an N+1. ``compute_item_metrics`` is the single-item convenience
wrapper the detail action uses.

The ``payload`` dict shape is the pinned contract consumed by
``InventoryMetricsSerializer`` and the ScanTTY worker — do not rename keys.
"""

from django.db.models import Q, Sum

from inventory.models import MaintenanceMaterial, WorkOrder, WorkOrderMaterialUsage
from inventory.services.pack_size import pack_size_of
from inventory.services.pricing import package_price_of, unit_price_of
from inventory.services.supplier_selection import primary_suppliers_for
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
    """Direction of the primary supplier's unit cost vs the most recent PO line.

    Both sides are compared with ``is None``, never truthiness: a supplier that
    dropped to ``0.00`` has moved DOWN, and a guard spelled ``if current and
    last`` would call that "no data" (op-9m2v).
    """
    if current_unit_cost is None or last_po_unit_cost is None:
        return "no_history"
    if current_unit_cost > last_po_unit_cost:
        return "up"
    if current_unit_cost < last_po_unit_cost:
        return "down"
    return "flat"


def _committed_by_item(item_ids):
    """Return ``({item_id: total}, {item_id: [breakdown_entry, ...]})`` (2 queries).

    QC is the open-work-order demand that has **not yet drawn down**
    ``current_stock``. Two kinds of row express that demand, and the rule that
    reconciles them is per **(work order, item)**:

    * **Actual usage rows** (:class:`WorkOrderMaterialUsage`) are authoritative
      for a work order that has materialised any for the item. An un-consumed
      row (``was_used`` False and no ``applied_quantity``) is demand; a consumed
      one contributes nothing, because its units have already left
      ``current_stock``. That is what stops a material consumed while its work
      order is still open from being subtracted twice — once from stock and
      again as committed.
    * **Template materials** (:class:`MaintenanceMaterial` on the work order's
      PM task) are the fallback for a work order that has not materialised
      usage rows for the item yet — one created straight from a template, or
      one whose rows were deleted.

    A template material is counted **at most once** even when its task carries
    several open work orders (a task with two open WOs needs the material once,
    not twice — the pre-existing rule), and is attributed to the oldest such
    work order that has not materialised usage rows for the item.

    The breakdown is the attribution side of the same numbers: one entry per
    (work order, item) pair holding a non-zero share of QC, so
    ``sum(entry["quantity"]) == total`` for every item. Entries are ordered
    oldest work order first.
    """
    # ``(item_id, work_order_id)`` -> committed quantity. A pair is fed by
    # EITHER usage rows or template materials, never both (see below).
    demand = {}
    # Pairs whose work order has materialised usage rows for the item — the
    # template fallback is switched off for exactly these.
    materialised = set()
    # work_order_id -> (created_at, asset_id, asset_name), for attribution.
    work_orders = {}

    # Actual usage on open work orders. ``stock_item`` prefers the row's own
    # ``inventory_item`` (how an ad-hoc line links stock) and falls back to the
    # template spec's; both columns come back so the same precedence can be
    # applied here, off one query, without loading rows. (1 query)
    for (
        direct_item_id,
        spec_item_id,
        work_order_id,
        quantity_used,
        was_used,
        applied_quantity,
        asset_id,
        asset_name,
        created_at,
    ) in (
        WorkOrderMaterialUsage.objects.filter(
            Q(inventory_item_id__in=item_ids)
            | Q(inventory_item__isnull=True, material__inventory_item_id__in=item_ids),
            work_order__status__in=OPEN_WO_STATUSES,
        )
        .values_list(
            "inventory_item_id",
            "material__inventory_item_id",
            "work_order_id",
            "quantity_used",
            "was_used",
            "applied_quantity",
            "work_order__asset_id",
            "work_order__asset__name",
            "work_order__created_at",
        )
        .order_by()  # clear Meta ordering; the rows are grouped in Python
    ):
        item_id = direct_item_id if direct_item_id is not None else spec_item_id
        work_orders[work_order_id] = (created_at, asset_id, asset_name)
        key = (item_id, work_order_id)
        materialised.add(key)
        if was_used or applied_quantity is not None:
            continue  # already consumed — these units are out of current_stock
        demand[key] = demand.get(key, 0.0) + float(quantity_used or 0)

    # Template materials on open work orders. DISTINCT over (material, work
    # order) so a task with several open work orders yields one candidate row
    # per work order — the material's quantity is still counted once, in the
    # reconciliation loop below. (1 query)
    template_candidates = {}
    for material_id, item_id, quantity, work_order_id, asset_id, asset_name, created_at in (
        MaintenanceMaterial.objects.filter(
            inventory_item_id__in=item_ids,
            maintenance_item__work_orders__status__in=OPEN_WO_STATUSES,
        )
        .values_list(
            "id",
            "inventory_item_id",
            "quantity",
            "maintenance_item__work_orders__id",
            "maintenance_item__work_orders__asset_id",
            "maintenance_item__work_orders__asset__name",
            "maintenance_item__work_orders__created_at",
        )
        .order_by()  # clear Meta ordering so DISTINCT dedupes purely by the row
        .distinct()
    ):
        work_orders[work_order_id] = (created_at, asset_id, asset_name)
        candidate = template_candidates.setdefault(material_id, (item_id, quantity, []))
        candidate[2].append(work_order_id)

    def _age(work_order_id):
        """Sort key: oldest work order first, id breaking a created_at tie."""
        return (work_orders[work_order_id][0], str(work_order_id))

    for item_id, quantity, candidate_work_orders in template_candidates.values():
        fallback = [wo for wo in candidate_work_orders if (item_id, wo) not in materialised]
        if not fallback:
            continue  # every open work order materialised its own rows
        key = (item_id, min(fallback, key=_age))
        demand[key] = demand.get(key, 0.0) + float(quantity or 0)

    totals = {}
    breakdown = {}
    for (item_id, work_order_id), quantity in sorted(demand.items(), key=lambda kv: _age(kv[0][1])):
        totals[item_id] = totals.get(item_id, 0.0) + quantity
        if not quantity:
            continue  # nothing committed here — don't clutter the attribution
        _created_at, asset_id, asset_name = work_orders[work_order_id]
        breakdown.setdefault(item_id, []).append(
            {
                "work_order_id": work_order_id,
                "work_order_short_id": WorkOrder.short_id_for(work_order_id),
                "asset_id": asset_id,
                "asset_name": asset_name,
                "quantity": quantity,
            }
        )

    return totals, breakdown


def compute_item_metrics_batch(items):
    """Return ``{item_id: metrics_payload}`` for ``items`` in bounded queries.

    ``items`` is an iterable of already-loaded ``InventoryItem`` instances
    (typically a single paginated page). The number of database queries is
    constant regardless of how many items are passed, so the list endpoint
    stays O(1) in queries rather than O(n): five grouped aggregates (on-order,
    in-transit, committed-from-usage, committed-from-template, last PO cost),
    plus a SIXTH for the supplier selection only when the caller has not
    prefetched ``item_suppliers`` — the list path has, so it pays five. Every
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

    # QIT — units still on their way, grouped by item. A subset of QOO
    # (partially_received is an on-order status). (1 query)
    #
    # "Still on their way" is the settlement question, so it is asked through
    # the line's own derivation rather than re-derived here. It used to be
    # spelled ``is_voided=False, quantity_received__lt=F("quantity_ordered")``,
    # which has no notion of a line closed short: units an operator had
    # explicitly written off as never arriving went on counting as in transit,
    # and inflated QIT could suppress a reorder for stock that was not coming.
    # ``outstanding()`` covers the voided case too, so nothing was lost.
    quantity_in_transit = {
        row["item_supplier__item"]: row["total"] or 0
        for row in (
            PurchaseOrderItem.objects.outstanding()
            .filter(
                item_supplier__item_id__in=item_ids,
                purchase_order__status=PurchaseOrder.Status.PARTIALLY_RECEIVED,
            )
            .values("item_supplier__item")
            .annotate(total=Sum(PurchaseOrderItem.outstanding_quantity_expression()))
            .order_by()  # clear Meta ordering so it can't contaminate GROUP BY
        )
    }

    # QC — quantity committed to open work orders, plus the per-work-order
    # attribution of it. Covers both the PM-template materials and the actual
    # (including ad-hoc) usage rows, reconciled so neither double-counts the
    # other — see ``_committed_by_item``. (2 queries)
    quantity_committed, committed_breakdown = _committed_by_item(item_ids)

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

    # The supplier to buy each item through (unit/package cost, lead time, case
    # size). Resolved through the shared derivation rather than re-ordered here,
    # so these numbers are the ones the item detail, the order pad and the PO
    # screens quote — and so an inactive or discontinued link never sets the
    # cost or lead time of a row that reads as buyable (op-2rsp). Batched, so
    # the per-item property never fires. (1 query, or ZERO when the caller
    # already prefetched ``item_suppliers`` — the list path does.)
    primary_supplier = primary_suppliers_for(items)

    for item in items:
        supplier = primary_supplier.get(item.id)
        # Cost through the ONE price derivation (op-9m2v), fed the row
        # ``primary_suppliers_for`` already resolved so the query budget below
        # is unchanged. No value moves here — this row was already ``None`` for
        # an unpriced or supplier-less item — but it is a READ of the column
        # and belongs on the derivation with every other one, so the next
        # reader of this loop inherits the guard rather than an ``or``.
        unit_cost = unit_price_of(supplier).amount
        package_cost = package_price_of(supplier).amount
        lead_time_days = supplier.average_lead_time if supplier else None
        # ``case_size`` — units per case from that same link, read through the
        # ONE pack-size derivation (op-c1ke) rather than off the column. Fed the
        # row ``primary_suppliers_for`` already resolved, so the query budget
        # above is unchanged. It was already ``None`` for an item with no
        # orderable supplier; it is now ``None`` for a link recording
        # ``quantity_per_package`` of 0 as well, because a box holding no units
        # is not a case size we know. ⚠️ ``case_size`` is the pinned ScanTTY
        # contract — the field is ``*int`` and null-tolerant there and nothing
        # reads the value — but this is a cross-project VALUE change and is
        # named as one in the PR.
        case_size = pack_size_of(supplier).units

        committed = quantity_committed.get(item.id, 0.0)
        # Cost shown on the row: the case cost for case-based items (what you
        # actually pay per package), else the per-unit cost.
        display_cost = package_cost if item.use_case_based_reorder else unit_cost

        result[item.id] = {
            "current_stock": item.current_stock,
            "quantity_on_order": quantity_on_order.get(item.id, 0),
            "quantity_available": float(item.current_stock) - committed,
            "quantity_committed": committed,
            "committed_breakdown": committed_breakdown.get(item.id, []),
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
