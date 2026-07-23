"""Purchase-order creation and status-transition workflow.

Extracted from ``reorder_queue.serializers`` and ``reorder_queue.views`` (#883).
The views keep the serializers as the request/response boundary and keep their
``record_audit_event`` calls; the workflow body lives here.

Note: PO number generation stays in ``PurchaseOrder.save()`` (owned by #887) —
``create_purchase_order`` relies on ``save()`` to number and retry on
uniqueness collisions.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone

from rest_framework import serializers

from inventory.models import ItemSupplier

from ..models import PurchaseOrder, PurchaseOrderItem, ReorderRequest


def _resolve_work_order(item_data, idx):
    """Resolve an optional ``work_order_id`` on a line into a WorkOrder (op-bu80).

    Orthogonal to the line's target — an inventory, asset or freeform line can
    all be "ordered to complete this job" — so it is resolved once, up front,
    for every branch below. Absent key → ``None``; unknown id → the same
    ValidationError shape the rest of this function raises.
    """
    work_order_id = item_data.get("work_order_id")
    if work_order_id in (None, ""):
        return None

    from inventory.models import WorkOrder

    try:
        return WorkOrder.objects.get(id=work_order_id)
    except (WorkOrder.DoesNotExist, DjangoValidationError, ValueError, TypeError):
        raise serializers.ValidationError(
            f"Item at index {idx}: work order {work_order_id} does not exist"
        )


@transaction.atomic
def create_purchase_order(validated_data, items_data, user):
    """Create a purchase order with line items (inventory items, assets, freeform).

    ``validated_data`` is the create serializer's validated data with ``items``
    already popped; ``items_data`` is that popped list. The caller (the create
    serializer) owns the non-empty / request-context / authenticated-user
    checks. Raises :class:`rest_framework.serializers.ValidationError` on any
    per-item validation failure, exactly as the serializer did.
    """
    # Create the purchase order; PurchaseOrder.save() auto-generates the
    # po_number and retries on uniqueness collisions (concurrent-create race).
    purchase_order = PurchaseOrder.objects.create(
        created_by=user,
        **validated_data,
    )

    # Create line items
    total_cost = Decimal("0.00")
    for idx, item_data in enumerate(items_data):
        quantity = item_data.get("quantity", 1)
        notes = item_data.get("notes", "")

        # Validate quantity
        if not isinstance(quantity, (int, float)) or quantity <= 0:
            raise serializers.ValidationError(
                f"Item at index {idx}: quantity must be a positive number, got {quantity}"
            )

        work_order = _resolve_work_order(item_data, idx)

        # Handle inventory items
        if "item_supplier_id" in item_data:
            item_supplier_id = item_data["item_supplier_id"]
            try:
                item_supplier = ItemSupplier.objects.get(id=item_supplier_id)

                # Ensure the supplier matches the PO supplier
                if item_supplier.supplier != purchase_order.supplier:
                    raise serializers.ValidationError(
                        f"Item supplier {item_supplier_id} does not belong to selected supplier"
                    )

                # Calculate order_in_packages: prefer explicit caller value
                # (frontend sends this when user enters whole cases), otherwise
                # ceil-divide the unit quantity by the supplier's case size.
                quantity_per_package = item_supplier.quantity_per_package or 1
                explicit_packages = item_data.get("order_in_packages")
                if explicit_packages is not None:
                    try:
                        order_in_packages = int(explicit_packages)
                    except (TypeError, ValueError):
                        raise serializers.ValidationError(
                            f"Item at index {idx}: order_in_packages must be "
                            f"an integer, got {explicit_packages!r}"
                        )
                    if order_in_packages < 0:
                        raise serializers.ValidationError(
                            f"Item at index {idx}: order_in_packages must be "
                            f"non-negative, got {order_in_packages}"
                        )
                else:
                    order_in_packages = (
                        (int(quantity) + quantity_per_package - 1) // quantity_per_package
                        if quantity_per_package > 0
                        else 0
                    )

                # Get unit_cost override if provided, otherwise use item_supplier.unit_cost
                unit_cost_override = item_data.get("unit_cost")
                if unit_cost_override is not None:
                    try:
                        unit_cost_ordered = Decimal(str(unit_cost_override))
                    except (InvalidOperation, ValueError):
                        raise serializers.ValidationError(
                            f"Item at index {idx}: unit_cost must be numeric, "
                            f"got {unit_cost_override!r}"
                        )
                else:
                    unit_cost_ordered = item_supplier.unit_cost or Decimal("0.00")

                # Get expected_shipment_date if provided
                expected_shipment_date = item_data.get("expected_shipment_date")

                # Create the line item
                line_item = PurchaseOrderItem.objects.create(
                    purchase_order=purchase_order,
                    item_supplier=item_supplier,
                    quantity_ordered=quantity,
                    unit_cost_ordered=unit_cost_ordered,
                    order_in_packages=order_in_packages,
                    notes=notes,
                    expected_shipment_date=expected_shipment_date,
                    work_order=work_order,
                )

                total_cost += line_item.estimated_cost

            except (ItemSupplier.DoesNotExist, ValueError, TypeError):
                raise serializers.ValidationError(
                    f"ItemSupplier with id {item_supplier_id} does not exist"
                )

        # Handle assets
        elif "asset_id" in item_data:
            asset_id = item_data["asset_id"]
            unit_cost = item_data.get("unit_cost")
            if unit_cost is None:
                raise serializers.ValidationError(
                    f"unit_cost is required when purchasing asset {asset_id}"
                )
            try:
                unit_cost = Decimal(str(unit_cost))
            except (InvalidOperation, ValueError):
                raise serializers.ValidationError(
                    f"Item at index {idx}: unit_cost must be numeric, " f"got {unit_cost!r}"
                )

            try:
                from inventory.models import Asset

                asset = Asset.objects.get(id=asset_id)

                # Ensure the asset's manufacturer matches the PO supplier (if set)
                if asset.manufacturer and asset.manufacturer != purchase_order.supplier:
                    raise serializers.ValidationError(
                        f"Asset {asset_id} manufacturer does not match selected supplier"
                    )

                # Create the line item (assets don't have package information)
                line_item = PurchaseOrderItem.objects.create(
                    purchase_order=purchase_order,
                    asset=asset,
                    quantity_ordered=quantity,
                    unit_cost_ordered=unit_cost,
                    order_in_packages=0,  # Assets don't have package information
                    notes=notes,
                    work_order=work_order,
                )

                total_cost += line_item.estimated_cost

            except (Asset.DoesNotExist, DjangoValidationError, ValueError):
                raise serializers.ValidationError(f"Invalid asset id: {asset_id}")

        # Handle freeform line items
        elif "description" in item_data:
            description = item_data["description"]
            unit_cost = item_data.get("unit_cost")

            if not description:
                raise serializers.ValidationError(
                    "Freeform items must have a non-empty description"
                )

            if unit_cost is None:
                raise serializers.ValidationError(
                    f"unit_cost is required for freeform item: {description}"
                )
            try:
                unit_cost = Decimal(str(unit_cost))
            except (InvalidOperation, ValueError):
                raise serializers.ValidationError(
                    f"Item at index {idx}: unit_cost must be numeric, " f"got {unit_cost!r}"
                )

            # Create the freeform line item (freeform items don't have package information)
            line_item = PurchaseOrderItem.objects.create(
                purchase_order=purchase_order,
                description=description,
                quantity_ordered=quantity,
                unit_cost_ordered=unit_cost,
                order_in_packages=0,  # Freeform items don't have package information
                notes=notes,
                work_order=work_order,
            )

            total_cost += line_item.estimated_cost

        else:
            raise serializers.ValidationError(
                "Each item must have 'item_supplier_id', 'asset_id', or 'description'"
            )

    # Update estimated total
    purchase_order.estimated_total = total_cost
    purchase_order.save()

    return purchase_order


def add_business_days(start_date, business_days):
    """Add business days to a date (excluding weekends)."""
    if isinstance(start_date, timezone.datetime):
        start_date = start_date.date()

    current_date = start_date
    days_added = 0

    while days_added < business_days:
        current_date += timedelta(days=1)
        # Monday = 0, Sunday = 6
        if current_date.weekday() < 5:  # Monday to Friday
            days_added += 1

    return current_date


def update_reorder_requests_from_po(purchase_order):
    """Update associated ReorderRequest objects when a PurchaseOrder is finalized.

    Updates requests with:
    - order_number (PO number)
    - actual_cost (from PO line items)
    - estimated_delivery (calculated from expected_delivery_date or lead time)
    - ordered_at (when PO was sent)
    - status = "ordered"
    """
    # Find all inventory items in this PO
    po_items = purchase_order.items.filter(item_supplier__isnull=False).select_related(
        "item_supplier__item"
    )

    for po_item in po_items:
        item = po_item.item_supplier.item

        # Find active reorder requests for this item (pending or approved)
        active_requests = ReorderRequest.objects.filter(
            item=item,
            status__in=[ReorderRequest.Status.PENDING, ReorderRequest.Status.APPROVED],
        )

        # Calculate estimated delivery date
        estimated_delivery = None
        if purchase_order.expected_delivery_date:
            estimated_delivery = purchase_order.expected_delivery_date
        elif po_item.item_supplier.average_lead_time:
            # Calculate from lead time in business days
            order_date = (
                purchase_order.sent_at.date() if purchase_order.sent_at else timezone.now().date()
            )
            lead_time_days = po_item.item_supplier.average_lead_time
            estimated_delivery = add_business_days(order_date, lead_time_days)

        # Update each active request
        for reorder_request in active_requests:
            reorder_request.status = ReorderRequest.Status.ORDERED
            reorder_request.order_number = purchase_order.po_number
            reorder_request.ordered_at = purchase_order.sent_at or timezone.now()
            reorder_request.estimated_delivery = estimated_delivery

            # Set actual cost if not already set
            if not reorder_request.actual_cost:
                # Calculate cost per unit from PO
                if po_item.quantity_ordered > 0 and po_item.unit_cost_ordered:
                    cost_per_unit = po_item.unit_cost_ordered
                    reorder_request.actual_cost = cost_per_unit * reorder_request.quantity
                else:
                    # Fallback to estimated cost from line item
                    reorder_request.actual_cost = po_item.estimated_cost

            reorder_request.save()


def mark_sent(purchase_order, user):
    """Stamp a purchase order as SENT and sync its linked reorder requests.

    Status -> SENT, ``sent_by``/``sent_at`` stamped, and the linked reorder
    requests synced. The caller owns the DRAFT precondition and records the
    ``po_send`` audit event.
    """
    purchase_order.status = PurchaseOrder.Status.SENT
    purchase_order.sent_by = user
    purchase_order.sent_at = timezone.now()
    purchase_order.save()
    # Keep linked reorder requests in step with the PO going out.
    update_reorder_requests_from_po(purchase_order)


def confirm_order(purchase_order, expected_delivery_date):
    """Mark a purchase order as confirmed by the supplier.

    The caller owns the SENT precondition.
    """
    purchase_order.status = PurchaseOrder.Status.CONFIRMED
    purchase_order.expected_delivery_date = expected_delivery_date
    purchase_order.save()


def void_po(purchase_order, user, reason=""):
    """Void an entire purchase order and cascade to its non-voided line items.

    The caller owns the permission/status guards and records the ``po_void``
    audit event.
    """
    now = timezone.now()

    with transaction.atomic():
        purchase_order.status = PurchaseOrder.Status.VOIDED
        purchase_order.voided_at = now
        purchase_order.voided_by = user
        purchase_order.void_reason = reason
        purchase_order.save()

        purchase_order.items.filter(is_voided=False).update(
            is_voided=True,
            voided_at=now,
            voided_by=user,
            void_reason="PO voided",
        )


def void_line_item(line_item, user, reason):
    """Void a single purchase-order line item.

    Marks the linked ``item_supplier`` discontinued/inactive when present. The
    caller owns the not-found / already-voided / already-received guards and
    records the ``po_line_void`` audit event.
    """
    line_item.is_voided = True
    line_item.voided_at = timezone.now()
    line_item.voided_by = user
    line_item.void_reason = reason

    # If this is an item_supplier relationship, mark it as discontinued
    if line_item.item_supplier:
        line_item.item_supplier.is_discontinued = True
        line_item.item_supplier.is_active = False
        line_item.item_supplier.save()

    line_item.save()
