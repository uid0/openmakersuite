"""Receiving/delivery workflow for purchase orders.

Extracted from ``reorder_queue.views`` (#883). Keeps the receipt side effects
(delivery + delivery items, per-line received quantity, inventory stock,
PO status advance, lead-time logging) in one transactional service so the
``receive`` and ``mark-delivered`` actions stay behaviour-identical.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction

from ..models import DeliveryItem, LeadTimeLog, OrderDelivery, PurchaseOrder


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
            DeliveryItem.objects.create(
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

            if po_item.is_fully_received:
                create_lead_time_log(po_item, delivery.delivery_date)

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
