"""Purchase-order creation and status-transition workflow.

Extracted from ``reorder_queue.serializers`` and ``reorder_queue.views`` (#883).
The views keep the serializers as the request/response boundary and keep their
``record_audit_event`` calls; the workflow body lives here.

Note: PO number generation stays in ``PurchaseOrder.save()`` (owned by #887) —
``create_purchase_order`` relies on ``save()`` to number and retry on
uniqueness collisions.

**Item packaging vs supplier packaging (op-ev14).** Two different pack sizes
meet on a PO line and the reconciliation between them is deliberate:

* ``ItemSupplier.quantity_per_package`` is the *supplier's* case — what that
  vendor actually ships. When a supplier declares one it **wins** for
  ``order_in_packages``: you buy what the vendor sells, and the line's package
  count has to match the vendor's catalogue for the order pad and package
  costing to mean anything. Unchanged behaviour.
* The item's own chain (:func:`inventory.services.packaging.order_level`, the
  outermost rung) fills in when the supplier declares **no** case size. Before
  op-ev14 such a line recorded ``order_in_packages == quantity_ordered``, which
  says nothing; a case-counted item now records how many of its OWN cases were
  ordered.
* ``quantity_ordered`` is always BASE units, whichever pack size shaped it — so
  on-hand, receipts and usage keep comparing like with like — and a caller may
  express it in the item's *count* unit with ``at_level`` on the line. That is
  the same unit every other op-ev14 path names (``on_hand_display``,
  ``count_unit``, the reconcile grid), never the supplier's case: the count unit
  is the one the API tells clients about.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone

from rest_framework import serializers

from inventory.models import ItemSupplier
from inventory.services.kits import build_kit_snapshot
from inventory.services.packaging import order_level, parse_at_level, resolve_base_quantity

from ..models import PurchaseOrder, PurchaseOrderItem, ReorderRequest
from .approvals import PO_ELIGIBLE_STATUSES


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


def order_package_size(item_supplier):
    """Base units in ONE package of ``item_supplier``, per the op-ev14 ladder.

    The supplier's case size when that supplier declares one, the item's own
    outermost packaging rung when it does not (see the module docstring for why
    that order), and ``1`` when neither pack size is declared — an item counted
    in base units has no rung, so one "package" is one unit.

    The single place that ladder is resolved. Every caller that needs to talk
    about a package — :func:`order_packages_for_line` converting a quantity into
    a package count, ``line_entry.repeat_quantity`` deciding how much one more
    scanned package is worth — reads it from here, so the two cannot drift into
    disagreeing about what a package is and leave a line whose package count and
    quantity describe different orders.

    Costs no extra query for an ``each`` item: :func:`order_level` reads
    ``count_mode`` and short-circuits before touching the chain.
    """
    quantity_per_package = item_supplier.quantity_per_package or 1
    if quantity_per_package > 1:
        return quantity_per_package

    rung = order_level(item_supplier.item)
    if rung is not None:
        return rung.base_units

    return 1


def order_packages_for_line(item_supplier, base_quantity):
    """How many packages ``base_quantity`` base units represents on a line.

    Ceil-divide by :func:`order_package_size`, which owns the supplier-case /
    item-rung / base-unit ladder (op-ev14). A base-unit item divides by 1, i.e.
    keeps the base-unit count itself.
    """
    return -(-base_quantity // order_package_size(item_supplier))


def _resolve_owning_group(item_data, idx):
    """Resolve an optional ``owning_group_id`` on a line into a Group (op-shb9).

    The committee twin of :func:`_resolve_work_order`, and equally orthogonal to
    the line's target — any of the three line kinds can be bought on behalf of a
    committee. Attribution only: it moves no stock and posts nothing to the
    ledger. Absent key → ``None``; unknown id → the same ValidationError shape.
    """
    owning_group_id = item_data.get("owning_group_id")
    if owning_group_id in (None, ""):
        return None

    from django.contrib.auth.models import Group

    try:
        return Group.objects.get(id=owning_group_id)
    except (Group.DoesNotExist, DjangoValidationError, ValueError, TypeError):
        raise serializers.ValidationError(
            f"Item at index {idx}: committee {owning_group_id} does not exist"
        )


@transaction.atomic
def create_purchase_order(validated_data, items_data, user):
    """Create a purchase order with line items (inventory items, assets, freeform).

    ``validated_data`` is the create serializer's validated data with ``items``
    already popped; ``items_data`` is that popped list. The caller (the create
    serializer) owns the non-empty / request-context / authenticated-user
    checks. Raises :class:`rest_framework.serializers.ValidationError` on any
    per-item validation failure, exactly as the serializer did.

    Header-level PO fields — including the optional ``supplier_agreement`` the
    order was placed under (op-yoos) and the optional order-level
    ``work_order``/``owning_group`` associations (op-shb9) — ride through
    ``validated_data`` onto the created ``PurchaseOrder``; the serializer owns
    validating that the agreement belongs to the PO's supplier.
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
        owning_group = _resolve_owning_group(item_data, idx)

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

                # ``at_level`` lets a caller order in the item's count unit
                # ("4 cases") instead of base units; the stored
                # ``quantity_ordered`` is always base units (op-ev14). Absent —
                # every caller today — the quantity passes through untouched.
                try:
                    quantity = resolve_base_quantity(
                        item_supplier.item,
                        int(quantity),
                        at_level=parse_at_level(item_data.get("at_level")),
                    )
                except DjangoValidationError as exc:
                    raise serializers.ValidationError(f"Item at index {idx}: {exc.messages[0]}")

                # Calculate order_in_packages: prefer explicit caller value
                # (frontend sends this when user enters whole cases), otherwise
                # derive it from the supplier's case size, falling back to the
                # item's own outermost packaging rung.
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
                    order_in_packages = order_packages_for_line(item_supplier, int(quantity))

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

                # Freeze what a kit contains right now (op-8n0). The BOM is
                # editable and receipt is days or weeks away, so the line has to
                # carry its own copy or receiving would credit today's recipe
                # for a box packed to the old one. ``None`` for ordinary items,
                # which is what keeps their stored row and payload unchanged.
                kit_snapshot = build_kit_snapshot(item_supplier.item)

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
                    owning_group=owning_group,
                    kit_snapshot=kit_snapshot,
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
                    owning_group=owning_group,
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
                owning_group=owning_group,
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


def apply_line_quantity(line_item, quantity):
    """Set ``quantity_ordered`` on a line and re-derive its package count.

    Does not save — the caller owns persistence (``update_item`` applies several
    fields to a line and saves it once). ``order_in_packages`` is re-derived
    through the same :func:`order_packages_for_line` ``create_purchase_order``
    uses, so an edited line records packages the same way the created one did;
    asset and freeform lines carry no package information and keep the 0 they
    were created with.

    ``quantity`` is BASE units — the caller owns any count-unit conversion (it
    also owns the value/voided/status/already-received guards, which compare
    against base-unit columns).
    """
    line_item.quantity_ordered = quantity
    if line_item.item_supplier_id is not None:
        line_item.order_in_packages = order_packages_for_line(line_item.item_supplier, quantity)
    return line_item


def recalculate_estimated_total(purchase_order):
    """Recompute and persist ``estimated_total`` from the PO's current lines.

    Editing a line's quantity moves its ``estimated_cost``, which the stored
    PO-level total was frozen from at create time. Voided lines stay in the
    stored total (``effective_estimated_total`` is what subtracts them), so the
    sum here matches what ``create_purchase_order`` wrote.

    Reads the lines back from the database instead of ``purchase_order.items``:
    the viewset prefetches ``items``, so the cached relation still holds the
    pre-edit quantities after a line is updated.
    """
    total = sum(
        (
            line.estimated_cost
            for line in PurchaseOrderItem.objects.filter(purchase_order=purchase_order)
        ),
        start=Decimal("0.00"),
    )
    purchase_order.estimated_total = total
    purchase_order.save(update_fields=["estimated_total", "updated_at"])
    return total


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

    Only *approved* requests are swept (op-tm70). Sending a PO used to close
    out pending requests for the same item too, which quietly marked an
    unapproved ask "ordered" — it would then vanish from the pending queue
    nobody had reviewed it in. A pending request is left exactly as it is.
    """
    # Find all inventory items in this PO
    po_items = purchase_order.items.filter(item_supplier__isnull=False).select_related(
        "item_supplier__item"
    )

    for po_item in po_items:
        item = po_item.item_supplier.item

        # Find the approved reorder requests this PO fulfils
        active_requests = ReorderRequest.objects.filter(
            item=item,
            status__in=PO_ELIGIBLE_STATUSES,
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


#: Sentinel for "the caller supplied no value", distinct from an explicitly
#: supplied ``None``. A confirm that names no delivery date must leave the one
#: the operator already set alone; a confirm that explicitly sends ``null`` is
#: asking to clear it. Collapsing the two silently erased the date on every
#: confirm from the web UI, which posts no body at all.
UNCHANGED = object()


def confirm_order(purchase_order, expected_delivery_date=UNCHANGED):
    """Mark a purchase order as confirmed by the supplier.

    The caller owns the SENT precondition.

    ``expected_delivery_date`` is written only when the caller actually supplies
    one. Omit it to confirm without touching the date already on the order —
    the delivery-anchored payment terms (``due_on_receipt`` / ``cod``) and
    :func:`reorder_queue.services.receiving.create_lead_time_log` both read that
    field, so overwriting it with a value nobody sent loses more than the field.
    """
    purchase_order.status = PurchaseOrder.Status.CONFIRMED
    if expected_delivery_date is not UNCHANGED:
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
