"""Receiving/delivery workflow for purchase orders.

Extracted from ``reorder_queue.views`` (#883). Keeps the receipt side effects
(delivery + delivery items, per-line received quantity, inventory stock,
PO status advance, lead-time logging) in one transactional service so the
``receive`` and ``mark-delivered`` actions stay behaviour-identical.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Iterable, NamedTuple, Optional, Sequence

from django.core.exceptions import ValidationError
from django.db import transaction

from ..models import DeliveryItem, LeadTimeLog, OrderDelivery, PurchaseOrder, ReorderRequest

if TYPE_CHECKING:  # pragma: no cover - typing only
    from inventory.models import InventoryItem


class SerialCapture(NamedTuple):
    """One serial-numbered unit being accessioned by a receipt.

    ``item`` names WHICH inventory identity the serial belongs to, and is the
    whole reason this carries an item at all rather than inheriting the line's:
    receiving a kit credits its COMPONENTS, so the serials on a kit line belong
    to components too. Writing them against the kit would attach serial numbers
    to a SKU that never enters stock and can never be drawn down — the
    data-corruption path ``InventoryItem._clean_kit`` refuses at the item level
    and :func:`resolve_serial_targets` refuses here.

    ``lot`` and ``expiration_date`` are optional provenance that ride along with
    the serial; both are recorded verbatim and neither affects stock.
    """

    item: "InventoryItem"
    serial_number: str
    lot: str = ""
    expiration_date: Optional[object] = None


class LineReceipt(NamedTuple):
    """One line's share of a receipt: how much arrived, and its serials."""

    po_item: object
    quantity: int
    serials: Sequence[SerialCapture] = ()


def as_line_receipt(entry) -> LineReceipt:
    """Normalise a caller's line entry to a :class:`LineReceipt`.

    Accepts the plain ``(po_item, quantity)`` pair that every pre-serials caller
    passes, so adding serial capture did not require touching paths that do not
    capture serials.
    """
    if isinstance(entry, LineReceipt):
        return entry
    po_item, quantity = entry
    return LineReceipt(po_item=po_item, quantity=quantity)


def serialized_receipt_targets(po_item, quantity: int) -> list[tuple["InventoryItem", int]]:
    """The identities a receipt of ``quantity`` on ``po_item`` may carry serials for.

    Returns ``[(item, units_credited), ...]`` covering only identities that are
    actually serialized, because those are the only ones a serial can be
    recorded against.

    * An ordinary inventory line offers its own item, ``quantity`` units.
    * A KIT line offers each serialized COMPONENT the receipt credits, for the
      component's own credited quantity (``quantity_per_kit x quantity``) — the
      kit itself is never offered. This is the single place the "serials belong
      to the component, never the kit" rule is expressed for receiving, and it
      reads the same order-time snapshot the stock credit reads, so the units a
      client is asked to serialize and the units that land in stock cannot
      disagree.
    * Asset and freeform lines have no inventory identity and offer nothing.
    """
    item = po_item.item
    if item is None:
        return []
    if item.is_kit:
        from inventory.services.kits import kit_component_credits

        return [
            (credit.component, credit.quantity)
            for credit in kit_component_credits(item, quantity, snapshot=po_item.kit_snapshot)
            if credit.component.is_serialized
        ]
    return [(item, quantity)] if item.is_serialized else []


def resolve_serial_targets(po_item, quantity: int, serials: Iterable[SerialCapture]) -> None:
    """Validate a line's serial captures against what the receipt actually credits.

    Raises :class:`ValidationError` — never silently drops a serial the operator
    entered, and never quietly re-points one at a different item.

    Refuses, in order:

    * a serial for an item this receipt does not credit (the kit's own identity
      included: naming the kit is the corruption path, and it is named in the
      error rather than being coerced to a component);
    * more serials for one identity than that identity has units in this
      receipt — the extras have nothing to be;
    * a duplicate serial number within one request.

    FEWER serials than units is allowed on purpose: goods that physically
    arrived must be recordable even when the operator has not scanned every
    unit yet. The gap stays visible as ``serials_outstanding`` on the line.
    """
    allowance = {item.pk: units for item, units in serialized_receipt_targets(po_item, quantity)}
    counts: dict[object, int] = {}
    seen: set[tuple[object, str]] = set()

    for capture in serials:
        target = capture.item
        if target.pk not in allowance:
            item = po_item.item
            if item is not None and item.is_kit and target.pk == item.pk:
                raise ValidationError(
                    f"Line {po_item.pk} buys the kit '{item.name}', which is never itself "
                    "stocked — record serials against the components the receipt credits, "
                    "not against the kit."
                )
            raise ValidationError(
                f"Serial '{capture.serial_number}' names item {target.pk}, which line "
                f"{po_item.pk} does not credit a serialized unit of in this receipt."
            )
        key = (target.pk, capture.serial_number)
        if key in seen:
            raise ValidationError(
                f"Serial '{capture.serial_number}' appears twice for the same item in "
                "this receipt."
            )
        seen.add(key)
        counts[target.pk] = counts.get(target.pk, 0) + 1
        if counts[target.pk] > allowance[target.pk]:
            raise ValidationError(
                f"{counts[target.pk]} serials were supplied for item {target.pk} but this "
                f"receipt only credits {allowance[target.pk]} unit(s) of it."
            )


def record_serials(delivery_item, po_item, serials: Iterable[SerialCapture], *, actor):
    """Accession each captured serial as a :class:`SerializedComponent`.

    Runs inside the receipt's transaction, so a serial that cannot be recorded
    rolls the whole receipt back rather than leaving stock credited with the
    operator's serials lost. Both provenance links are written — the delivery
    line the unit came in on, and the purchase-order line it was ordered
    against — which is what lets "where did this unit come from?" be answered
    from the unit itself.

    A serial already on file for that item is a hard error: it is either a
    double-scan or two physical units stamped alike, and both need a human.
    """
    from inventory.models import SerializedComponent

    created = []
    for capture in serials:
        if SerializedComponent.objects.filter(
            item=capture.item, serial_number=capture.serial_number
        ).exists():
            raise ValidationError(
                f"Serial '{capture.serial_number}' is already recorded against "
                f"{capture.item.name}."
            )
        component = SerializedComponent.objects.create(
            item=capture.item,
            serial_number=capture.serial_number,
            lot=capture.lot or "",
            expiration_date=capture.expiration_date,
            provenance_delivery_item=delivery_item,
            provenance_purchase_order_item=po_item,
        )
        component.apply_action(SerializedComponent.Action.RECEIVE, actor=actor)
        created.append(component)
    return created


def close_linked_reorder_request(po_item, delivery_date, *, item=None):
    """Close the item's open reorder requests when a PO line is fully received.

    The requests that started the purchase are what a member actually watches:
    receiving the parts through the purchase-order workflow used to leave them
    sitting in the reorder queue until somebody separately hit their
    ``mark_received`` action. Fully receiving the line now closes them.

    Deliberately **status-only bookkeeping**: the caller has already posted the
    received quantity to ``item.current_stock``, and
    :meth:`ReorderRequestViewSet.mark_received` only increments stock because it
    is a standalone path with no receipt behind it. Adding the request quantity
    here as well would double-count the delivery.

    Matching is by inventory item — there is no FK from a purchase order (or its
    lines) to a reorder request. :func:`update_reorder_requests_from_po` marks
    *every approved* request for an item as ordered when the PO is sent, so
    several concurrent requests for one item can legitimately be open; receiving
    the line closes **all** of them (every pending/approved/ordered request for
    the item). Deliberately wider than the approved-only ordering gate
    (op-tm70): approval decides what may be *bought*, but once the goods are
    physically on the shelf a still-pending ask for that item has been
    satisfied too, and leaving it open would have someone order it twice.
    Already-received or cancelled requests are never touched, so re-driving the
    same receipt is a no-op.

    ``item`` overrides which inventory item's requests are closed, for kit lines
    (op-8n0). A kit line's ``.item`` is the KIT, and nobody files a reorder
    request against a kit — requests are filed against "cyan ink". Left to the
    default, receiving a kit would silently leave every component's request open
    and somebody would re-order cyan next week. The kit caller therefore invokes
    this once per exploded component. Still status-only bookkeeping either way:
    the component's stock was already credited by ``explode_kit_receipt``.

    Returns the list of requests it closed — empty for asset-only or freeform
    lines, which have no inventory item, and for an item with nothing
    outstanding.
    """
    inventory_item = item if item is not None else po_item.item
    if inventory_item is None:
        return []

    active_requests = list(
        inventory_item.reorder_requests.filter(
            status__in=[
                ReorderRequest.Status.PENDING,
                ReorderRequest.Status.APPROVED,
                ReorderRequest.Status.ORDERED,
            ]
        )
    )
    if not active_requests:
        return []

    received_on = delivery_date.date() if hasattr(delivery_date, "date") else delivery_date
    purchase_order = po_item.purchase_order
    reference = purchase_order.po_number or purchase_order.pk
    note = f"Auto-received via PO {reference} on {received_on:%Y-%m-%d}."

    for reorder_request in active_requests:
        reorder_request.status = ReorderRequest.Status.RECEIVED
        reorder_request.actual_delivery = received_on
        reorder_request.admin_notes = f"{reorder_request.admin_notes}\n{note}".strip()
        reorder_request.save(
            update_fields=["status", "actual_delivery", "admin_notes", "updated_at"],
        )
    return active_requests


def create_lead_time_log(po_item, delivery_date):
    """Create a LeadTimeLog entry when a PO item is fully received.

    No-op if the PO was never sent or if the item has no item_supplier
    (e.g. asset-only lines).
    """
    purchase_order = po_item.purchase_order

    if not purchase_order.sent_at or not po_item.item_supplier:
        return

    order_date = purchase_order.sent_at
    actual_delivery_date = delivery_date.date() if hasattr(delivery_date, "date") else delivery_date

    estimated_lead_time = po_item.item_supplier.average_lead_time or 14
    actual_lead_time = LeadTimeLog.calculate_business_days(order_date, actual_delivery_date)

    LeadTimeLog.objects.create(
        item_supplier=po_item.item_supplier,
        purchase_order=purchase_order,
        order_date=order_date,
        expected_delivery_date=purchase_order.expected_delivery_date
        or (order_date.date() + timedelta(days=estimated_lead_time)),
        actual_delivery_date=actual_delivery_date,
        estimated_lead_time_days=estimated_lead_time,
        actual_lead_time_days=actual_lead_time,
        quantity_ordered=po_item.quantity_ordered,
        quantity_received=po_item.quantity_received,
    )


def receive_delivery(
    purchase_order,
    line_quantities,
    *,
    received_by,
    delivery_datetime,
    tracking_number="",
    carrier="",
    receipt_notes="",
):
    """Record a receipt of specific quantities against PO line items.

    Creates a single :class:`OrderDelivery` plus one :class:`DeliveryItem` per
    line entry, increments each line's received quantity and the linked
    inventory stock, accessions any captured serials, advances the PO status,
    and writes a :class:`LeadTimeLog` for any line that becomes fully received.

    ``line_quantities`` is an iterable of :class:`LineReceipt` — or of the plain
    ``(po_item, quantity)`` pairs every pre-serials caller passes, which
    :func:`as_line_receipt` normalises.

    Shared by ``mark_delivered`` (which passes every pending quantity) and the
    per-item ``receive`` action so receipt side effects stay consistent (DRY).
    The delivery is flagged ``is_complete`` when this receipt leaves receiving
    finished with the whole PO — for ``mark_delivered`` that is always the case,
    matching its previous behaviour.

    **Quantities are recorded as given, including more than was ordered.** An
    over-receipt credits the stock that physically arrived and leaves the
    difference visible on the line (``quantity_variance``,
    ``receipt_state=over_received``); it is never rounded down to the ordered
    figure. Callers own the rest of the validation of ``line_quantities``; the
    transaction is owned here. Returns the created :class:`OrderDelivery`.
    """
    receipts = [as_line_receipt(entry) for entry in line_quantities]

    with transaction.atomic():
        delivery = OrderDelivery.objects.create(
            purchase_order=purchase_order,
            delivery_date=delivery_datetime,
            tracking_number=tracking_number,
            carrier=carrier,
            received_by=received_by,
            receipt_notes=receipt_notes,
        )

        for receipt in receipts:
            po_item, quantity = receipt.po_item, receipt.quantity
            resolve_serial_targets(po_item, quantity, receipt.serials)

            delivery_item = DeliveryItem.objects.create(
                delivery=delivery,
                purchase_order_item=po_item,
                quantity_received=quantity,
            )
            record_serials(delivery_item, po_item, receipt.serials, actor=received_by)

            po_item.quantity_received += quantity
            po_item.save()

            inventory_item = po_item.item
            kit_credits = []
            if inventory_item is not None:
                # A kit is the SKU that was bought, but not the thing that goes
                # on the shelf: receiving it credits its component items and
                # leaves the kit's own stock at zero (op-8n0). Driven by THIS
                # receipt's ``quantity``, never ``po_item.quantity_received``,
                # so partial receipts stay additive and an over-receipt credits
                # the components of the kits that actually turned up.
                #
                # Credits the line's ORDER-TIME snapshot, so a kit edited between
                # ordering and delivery still credits what is in the box. Only a
                # legacy line with no snapshot reads the kit's live components.
                if inventory_item.is_kit:
                    from inventory.services.kits import explode_kit_receipt

                    kit_credits = explode_kit_receipt(
                        inventory_item, quantity, snapshot=po_item.kit_snapshot
                    )
                else:
                    inventory_item.current_stock += quantity
                    inventory_item.save()

                # Committee-owned purchasing hits the ledger (Phase 2 · Bead 5):
                # DR 1300 Inventory (dim=committee) / CR 2000 Accounts Payable
                # for quantity × unit cost, in this same receive transaction. An
                # item with no owning committee or no unit cost posts nothing, so
                # ordinary receives behave exactly as before. Local import avoids
                # a reorder_queue <-> accounting import cycle.
                if inventory_item.owning_group_id:
                    unit_cost = po_item.unit_cost_actual or po_item.unit_cost_ordered
                    if unit_cost:
                        from accounting.adapters import post_po_receipt

                        post_po_receipt(
                            committee=inventory_item.owning_group,
                            amount=quantity * unit_cost,
                            source_ref=f"po_receipt:{delivery_item.id}",
                            item=inventory_item,
                            created_by=received_by,
                        )

            # Ordered-for-a-job lines thread back onto that job (op-bu80):
            # the received quantity and its actual cost become a material line
            # on the work order, feeding ``WorkOrder.actual_material_cost``.
            # No work order on the line → nothing happens, and the bridge
            # never moves stock, so ordinary receives are untouched. Local
            # import avoids a reorder_queue <-> inventory services cycle.
            if po_item.work_order_id:
                from inventory.services.work_order_purchase_bridge import post_work_order_material

                post_work_order_material(po_item)

            if po_item.is_fully_received:
                create_lead_time_log(po_item, delivery.delivery_date)
                # The whole line landed, so whatever reorder request asked for
                # it is satisfied — close it in the same transaction as the
                # receipt. A partial receipt leaves it open.
                if kit_credits:
                    # Requests are filed against the components, never the kit,
                    # so close each exploded component's instead of the kit's.
                    for credit in kit_credits:
                        close_linked_reorder_request(
                            po_item, delivery.delivery_date, item=credit.component
                        )
                else:
                    close_linked_reorder_request(po_item, delivery.delivery_date)

        # ``is_settled``, not ``is_fully_received``: a line whose shortfall has
        # been written off is finished with even though it never got its full
        # ordered quantity, and an order made only of such lines must be able
        # to reach ``received``. The shortfall itself stays on the record via
        # ``has_receipt_variance``.
        if purchase_order.is_settled:
            purchase_order.status = PurchaseOrder.Status.RECEIVED
        else:
            purchase_order.status = PurchaseOrder.Status.PARTIALLY_RECEIVED
        purchase_order.save()

        delivery.is_complete = purchase_order.is_settled
        delivery.save(update_fields=["is_complete"])

    return delivery


def mark_delivered_receipt(
    purchase_order,
    *,
    received_by,
    delivery_datetime,
    tracking_number="",
    carrier="",
    receipt_notes="",
):
    """Receive every still-pending quantity on the PO as a single delivery.

    Thin wrapper over :func:`receive_delivery` used by the ``mark-delivered``
    action: every line that is not yet fully received is receipted for its
    outstanding quantity. Callers own the "already fully received" guard.
    """
    pending_items = [item for item in purchase_order.items.all() if not item.is_fully_received]
    line_quantities = [(po_item, po_item.quantity_pending) for po_item in pending_items]
    return receive_delivery(
        purchase_order,
        line_quantities,
        received_by=received_by,
        delivery_datetime=delivery_datetime,
        tracking_number=tracking_number,
        carrier=carrier,
        receipt_notes=receipt_notes,
    )


def refresh_receipt_status(purchase_order) -> str:
    """Re-derive and persist the order's status from its lines' settlement.

    Called after anything that can change whether receiving is finished with a
    line but is not itself a receipt — closing a line short, principally. Only
    ever moves an order that is *in* receiving: a draft, cancelled or voided
    order is left exactly where it is, because "every line is settled" is not a
    reason to resurrect an order nobody is receiving against.

    Returns the resulting status.
    """
    if purchase_order.status not in PurchaseOrder.RECEIVABLE_STATUSES | {
        PurchaseOrder.Status.RECEIVED
    }:
        return purchase_order.status

    # Drop the per-instance aggregate cache: the caller has just mutated lines
    # through it, and a stale read here is exactly how an order finishes
    # receiving and stays displayed as partially received.
    purchase_order.__dict__.pop("_line_item_totals", None)
    purchase_order.status = (
        PurchaseOrder.Status.RECEIVED
        if purchase_order.is_settled
        else PurchaseOrder.Status.PARTIALLY_RECEIVED
    )
    purchase_order.save(update_fields=["status", "updated_at"])
    return purchase_order.status


def close_lines_short(purchase_order, closures, *, actor):
    """Write off the outstanding balance on each named line, then re-derive status.

    ``closures`` is an iterable of ``(po_item, reason)``. Every closure happens
    in one transaction with the status refresh, so an order can never be left
    with some lines written off and a status that still claims it is waiting on
    them.

    Returns the lines that were closed.
    """
    closed = []
    with transaction.atomic():
        for po_item, reason in closures:
            po_item.close_short(actor=actor, reason=reason)
            closed.append(po_item)
        refresh_receipt_status(purchase_order)
    return closed


def outstanding_lines(purchase_order):
    """The active lines receiving is still waiting on, in the order's own order.

    Derived from each line's :attr:`~reorder_queue.models.PurchaseOrderItem.is_settled`
    rather than from a status list, so a line that becomes settled by a route
    added later drops out of here without this function being touched.
    """
    return [item for item in purchase_order.items.all() if not item.is_settled]


def line_scan_codes(po_item) -> list[dict]:
    """The identifiers a scanner could read off this line's goods.

    ``[{"code": ..., "kind": ...}, ...]``, deduplicated and in a stable order.
    Kinds are ``item_sku`` (our own SKU), ``package_upc`` / ``unit_upc`` (the
    barcodes on the outer box and on a single unit) and ``supplier_sku`` (the
    vendor's number, which is what appears on a vendor-applied label).

    Blank identifiers are omitted rather than emitted as empty strings: a
    client matching a scan against these must not match every unlabelled line
    on a scan of "". Asset and freeform lines contribute nothing and come back
    empty, which is a real answer — "this line cannot be scanned to" — and not
    the same as "no match found".
    """
    codes: list[dict] = []
    seen: set[str] = set()

    def add(code, kind):
        cleaned = (code or "").strip()
        if not cleaned or cleaned in seen:
            return
        seen.add(cleaned)
        codes.append({"code": cleaned, "kind": kind})

    item = po_item.item
    if item is not None:
        add(item.sku, "item_sku")
    item_supplier = po_item.item_supplier
    if item_supplier is not None:
        add(item_supplier.package_upc, "package_upc")
        add(item_supplier.unit_upc, "unit_upc")
        add(item_supplier.supplier_sku, "supplier_sku")
    return codes


def _worksheet_unavailable_reason(purchase_order) -> Optional[str]:
    """Why this order cannot be received against, or ``None`` when it can.

    Named states rather than a bare "unavailable": an operator standing at the
    bench with a box needs to know whether to send the order, or whether they
    already received it. Derived as the complement of
    ``PurchaseOrder.RECEIVABLE_STATUSES``, so the reasons and the gate cannot
    disagree about which statuses are receivable.
    """
    if purchase_order.status in PurchaseOrder.RECEIVABLE_STATUSES:
        return None
    reasons = {
        PurchaseOrder.Status.DRAFT: (
            "This order is still a draft. Send it to the supplier before receiving against it."
        ),
        PurchaseOrder.Status.RECEIVED: "Receiving has finished with every line on this order.",
        PurchaseOrder.Status.CANCELLED: (
            "This order was cancelled, so nothing can be received against it."
        ),
        PurchaseOrder.Status.VOIDED: (
            "This order was voided, so nothing can be received against it."
        ),
    }
    return reasons.get(
        purchase_order.status,
        f"This order is {purchase_order.get_status_display()}, which cannot receive items.",
    )


def build_receiving_worksheet(purchase_order) -> dict:
    """The receive screen's whole payload, derived from the order.

    Everything here is computed from the order and its lines on each read — no
    stored worksheet, nothing to invalidate, and a receipt recorded by another
    client shows up the next time this is fetched.

    ``can_receive`` plus ``unavailable_reason`` are a deliberate pair: a client
    must be able to tell "you may not receive against this, and here is why"
    from "there is nothing outstanding", because an operator acts differently
    on each. Lines are reported whether outstanding or settled, each carrying
    its own ``receipt_state``, so a partially received order is unambiguous
    about which lines are still owed.
    """
    lines = []
    for po_item in purchase_order.items.all():
        quantity_ordered = po_item.quantity_ordered or 0
        lines.append(
            {
                "purchase_order_item": po_item.id,
                "label": po_item.target_label,
                "item": str(po_item.item.pk) if po_item.item is not None else None,
                "item_type": po_item.target_type,
                "quantity_ordered": quantity_ordered,
                "quantity_received": po_item.quantity_received,
                "quantity_pending": po_item.quantity_pending,
                "quantity_variance": po_item.quantity_variance,
                "receipt_state": po_item.receipt_state,
                "receipt_state_label": po_item.receipt_state_label,
                "is_settled": po_item.is_settled,
                "is_voided": po_item.is_voided,
                "is_closed_short": po_item.is_closed_short,
                "closed_short_reason": po_item.closed_short_reason,
                "is_kit_line": po_item.is_kit_line,
                "scan_codes": line_scan_codes(po_item),
                "serial_targets": [
                    {
                        "item": str(item.pk),
                        "item_name": item.name,
                        "item_sku": item.sku,
                        "serial_tracking_mode": item.serial_tracking_mode,
                        "quantity": units,
                    }
                    for item, units in serialized_receipt_targets(po_item, quantity_ordered)
                ],
                "serials_recorded": po_item.serialized_components.count(),
            }
        )

    return {
        "purchase_order": purchase_order.id,
        "po_number": purchase_order.po_number,
        "supplier": purchase_order.supplier.name,
        "status": purchase_order.status,
        "status_label": purchase_order.get_status_display(),
        "can_receive": purchase_order.status in PurchaseOrder.RECEIVABLE_STATUSES,
        "unavailable_reason": _worksheet_unavailable_reason(purchase_order),
        "is_settled": purchase_order.is_settled,
        "is_fully_received": purchase_order.is_fully_received,
        "has_receipt_variance": purchase_order.has_receipt_variance,
        "outstanding_line_count": purchase_order.outstanding_line_count,
        "variance_line_count": purchase_order.variance_line_count,
        "lines": lines,
    }
