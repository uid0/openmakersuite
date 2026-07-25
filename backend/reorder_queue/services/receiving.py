"""Receiving/delivery workflow for purchase orders.

Extracted from ``reorder_queue.views`` (#883). Keeps the receipt side effects
(delivery + delivery items, per-line received quantity, inventory stock,
PO status advance, lead-time logging) in one transactional service so the
``receive`` and ``mark-delivered`` actions stay behaviour-identical.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction

from ..models import DeliveryItem, LeadTimeLog, OrderDelivery, PurchaseOrder, ReorderRequest


def close_linked_reorder_request(po_item, delivery_date):
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
    *every* active request for an item as ordered when the PO is sent, so several
    concurrent requests for one item can legitimately be open; receiving the line
    closes **all** of them (every pending/approved/ordered request for the item).
    Already-received or cancelled requests are never touched, so re-driving the
    same receipt is a no-op.

    Returns the list of requests it closed — empty for asset-only or freeform
    lines, which have no inventory item, and for an item with nothing
    outstanding.
    """
    inventory_item = po_item.item
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
    ``(po_item, quantity)`` pair, increments each line's received quantity and
    the linked inventory stock, advances the PO status, and writes a
    :class:`LeadTimeLog` for any line that becomes fully received.

    Shared by ``mark_delivered`` (which passes every pending quantity) and the
    per-item ``receive`` action so receipt side effects stay consistent (DRY).
    The delivery is flagged ``is_complete`` when this receipt leaves the whole
    PO fully received — for ``mark_delivered`` that is always the case, matching
    its previous behaviour.

    Callers are responsible for validating ``line_quantities``; the transaction
    is owned here. Returns the created :class:`OrderDelivery`.
    """
    with transaction.atomic():
        delivery = OrderDelivery.objects.create(
            purchase_order=purchase_order,
            delivery_date=delivery_datetime,
            tracking_number=tracking_number,
            carrier=carrier,
            received_by=received_by,
            receipt_notes=receipt_notes,
        )

        for po_item, quantity in line_quantities:
            delivery_item = DeliveryItem.objects.create(
                delivery=delivery,
                purchase_order_item=po_item,
                quantity_received=quantity,
            )

            po_item.quantity_received += quantity
            po_item.save()

            inventory_item = po_item.item
            if inventory_item is not None:
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
                from inventory.services.work_order_purchase_bridge import (
                    post_work_order_material,
                )

                post_work_order_material(po_item)

            if po_item.is_fully_received:
                create_lead_time_log(po_item, delivery.delivery_date)
                # The whole line landed, so whatever reorder request asked for
                # it is satisfied — close it in the same transaction as the
                # receipt. A partial receipt leaves it open.
                close_linked_reorder_request(po_item, delivery.delivery_date)

        if purchase_order.is_fully_received:
            purchase_order.status = PurchaseOrder.Status.RECEIVED
        else:
            purchase_order.status = PurchaseOrder.Status.PARTIALLY_RECEIVED
        purchase_order.save()

        delivery.is_complete = purchase_order.is_fully_received
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
