"""
Serializers for reorder queue API.
"""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from rest_framework import serializers

from inventory.serializers import InventoryItemSerializer, ItemSupplierSerializer

from .models import (
    DeliveryItem,
    LeadTimeLog,
    OrderDelivery,
    PurchaseOrder,
    PurchaseOrderAttachment,
    PurchaseOrderItem,
    ReorderRequest,
    WebHook,
)
from .services import create_purchase_order


class ReorderRequestSerializer(serializers.ModelSerializer):
    item_details = InventoryItemSerializer(source="item", read_only=True)
    estimated_cost = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    days_pending = serializers.IntegerField(read_only=True)
    reviewed_by_username = serializers.CharField(
        source="reviewed_by.username", read_only=True, allow_null=True
    )

    class Meta:
        model = ReorderRequest
        fields = [
            "id",
            "item",
            "item_details",
            "quantity",
            "status",
            "priority",
            "requested_by",
            "request_notes",
            "requested_at",
            "reviewed_by",
            "reviewed_by_username",
            "reviewed_at",
            "admin_notes",
            "ordered_at",
            "estimated_delivery",
            "actual_delivery",
            "order_number",
            "actual_cost",
            "estimated_cost",
            "days_pending",
            "updated_at",
            # Transparency fields
            "invoice_number",
            "invoice_url",
            "purchase_order_url",
            "delivery_tracking_url",
            "supplier_url",
            "public_notes",
        ]
        # The workflow state and the stamps that go with it are owned by the
        # ``approve`` / ``mark_ordered`` / ``mark_received`` / ``cancel``
        # actions — that is where the permission checks and the timestamping
        # live. Left writable, ``PATCH {"status": "approved"}`` let any
        # authenticated member sign off their own request without passing the
        # approver gate, and landed the row approved with a NULL reviewer —
        # which is exactly what makes it eligible to be purchased (op-xj1i,
        # the side door left open by op-tm70). Read-only here means a write to
        # one of these is ignored rather than applied; the response body still
        # echoes the true state.
        read_only_fields = [
            "requested_at",
            "updated_at",
            "status",
            "reviewed_by",
            "reviewed_at",
            "ordered_at",
        ]


class ReorderRequestCreateSerializer(serializers.ModelSerializer):
    """Simplified serializer for creating reorder requests (public-facing).

    Used as both the input serializer for the public ``create`` endpoint
    and the output shape for anonymous callers so no admin metadata
    (admin_notes, invoice_url, supplier_url, actual_cost, …) ever leaks
    back to an anonymous caller. ``id`` and ``status`` are exposed
    read-only so the QR-scan flow can confirm the row landed and which
    state it's in (typically ``pending``).
    """

    class Meta:
        model = ReorderRequest
        fields = ["id", "item", "quantity", "requested_by", "request_notes", "priority", "status"]
        read_only_fields = ["id", "status"]
        extra_kwargs = {
            "requested_by": {"required": False},
            "request_notes": {"required": False},
            "priority": {"required": False},
        }


# Purchase Order Serializers


def work_order_identity(work_order):
    """Minimal identity block for an associated work order, or ``None``.

    Shared by the line (op-bu80) and order (op-shb9) serializers so both render
    the same four fields without a second fetch.
    """
    if work_order is None:
        return None
    return {
        "id": str(work_order.id),
        "short_id": work_order.short_id,
        "display_title": work_order.display_title,
        "status": work_order.status,
    }


def owning_group_identity(group):
    """Minimal ``{id, name}`` for an associated committee (SIG), or ``None``.

    The committee is an ``auth.Group``; ``name`` is what the SIG pickers label
    it with, so nothing else is needed to render the association (op-shb9).
    """
    if group is None:
        return None
    return {"id": group.id, "name": group.name}


class PurchaseOrderItemSerializer(serializers.ModelSerializer):
    """Serializer for purchase order line items."""

    # Related data
    item_details = InventoryItemSerializer(source="item", read_only=True)
    supplier_details = serializers.CharField(
        source="supplier.name", read_only=True, allow_null=True
    )
    item_supplier_details = ItemSupplierSerializer(source="item_supplier", read_only=True)
    asset_details = serializers.SerializerMethodField()
    item_type = serializers.SerializerMethodField()
    # op-bu80: "ordered for this work order". ``work_order`` itself is writable
    # so a line can be tagged (or untagged) after the fact; the details block is
    # what a PO screen renders without a second fetch.
    work_order_details = serializers.SerializerMethodField()
    # op-shb9: "ordered on behalf of this committee", the line-level twin of the
    # work-order tag above. Both are set through ``update_item``.
    owning_group_details = serializers.SerializerMethodField()

    # Calculated fields
    estimated_cost = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    actual_cost = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    quantity_pending = serializers.IntegerField(read_only=True)
    is_fully_received = serializers.BooleanField(read_only=True)
    # Receiving state (oms-po-receiving). All DERIVED from the quantities and
    # the close-short stamp, so no client has to re-implement "is this line
    # short?" and none of them can reach a different answer.
    #
    # ``quantity_variance`` is the signed difference between what arrived and
    # what was ordered — negative short, positive over — and is the honest
    # figure ``quantity_pending`` deliberately floors away.
    quantity_variance = serializers.IntegerField(read_only=True)
    receipt_state = serializers.CharField(read_only=True)
    receipt_state_label = serializers.CharField(read_only=True)
    is_settled = serializers.BooleanField(read_only=True)
    has_receipt_variance = serializers.BooleanField(read_only=True)
    is_over_received = serializers.BooleanField(read_only=True)
    is_short_received = serializers.BooleanField(read_only=True)
    is_closed_short = serializers.BooleanField(read_only=True)
    closed_short_by_username = serializers.CharField(
        source="closed_short_by.username", read_only=True, allow_null=True
    )
    # A close-short taken back. Reported ALONGSIDE the close-short it corrects,
    # never instead of it: both sets of stamps stay on the line so the history
    # reads as a mistake and its correction. ``is_closed_short`` above is
    # derived from the two together, so a client never has to compare them.
    was_reopened = serializers.BooleanField(read_only=True)
    reopened_by_username = serializers.CharField(
        source="reopened_by.username", read_only=True, allow_null=True
    )
    # Which identities a receipt on this line may carry serials for, and how
    # many units of each the ordered quantity implies — the kit's COMPONENTS on
    # a kit line, never the kit. Rendered from the same function the receipt
    # validates against, so a client is never asked for a serial the receipt
    # would then refuse.
    serial_targets = serializers.SerializerMethodField()
    serials_recorded = serializers.SerializerMethodField()
    # Units of a serialized identity this line has already put on the shelf
    # that still carry no serial number. What replaced the old ban on
    # serialized kit components: the gap is reported rather than the
    # configuration refused, and it covers every receive path — including
    # ``mark-delivered``, which credits stock and records no serials at all.
    serials_outstanding = serializers.SerializerMethodField()
    # Kit lines (op-8n0). ``is_kit_line`` is derived from the item this line
    # already points at, and ``kit_components`` previews what receiving the
    # ordered quantity will credit.
    is_kit_line = serializers.BooleanField(read_only=True)
    kit_components = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseOrderItem
        fields = [
            "id",
            "purchase_order",
            "item_supplier",
            "asset",
            "description",
            "work_order",
            "work_order_details",
            "owning_group",
            "owning_group_details",
            "item_supplier_details",
            "item_details",
            "asset_details",
            "item_type",
            "supplier_details",
            "quantity_ordered",
            "quantity_received",
            "unit_cost_ordered",
            "unit_cost_actual",
            "estimated_cost",
            "actual_cost",
            "quantity_pending",
            "quantity_variance",
            "is_fully_received",
            "receipt_state",
            "receipt_state_label",
            "is_settled",
            "has_receipt_variance",
            "is_over_received",
            "is_short_received",
            "is_closed_short",
            "closed_short_at",
            "closed_short_by",
            "closed_short_by_username",
            "closed_short_reason",
            "was_reopened",
            "reopened_at",
            "reopened_by",
            "reopened_by_username",
            "reopened_reason",
            "serial_targets",
            "serials_recorded",
            "serials_outstanding",
            "is_kit_line",
            "kit_components",
            "expected_shipment_date",
            "actual_shipment_date",
            "is_voided",
            "voided_at",
            "voided_by",
            "void_reason",
            "notes",
            "created_at",
            "updated_at",
        ]
        # ``unit_cost_ordered`` is read-only here so the price-trace invariant
        # holds by construction rather than by accident of routing: nothing
        # currently feeds this serializer input, but a future viewset that did
        # would otherwise rewrite a line's ordered price with no audit event.
        # The two routes that may change it — the draft-only PATCH reprice and
        # an add that grows a line — both record what they replaced.
        read_only_fields = [
            "created_at",
            "updated_at",
            "voided_at",
            "voided_by",
            "unit_cost_ordered",
            # Owned by the close-short / mark-received actions, which is where
            # the actor and the reason are stamped together — and by
            # reopen-short, which stamps its own correction beside them.
            "closed_short_at",
            "closed_short_by",
            "closed_short_reason",
            "reopened_at",
            "reopened_by",
            "reopened_reason",
        ]

    def get_serial_targets(self, obj):
        """Identities a receipt on this line may record serials against.

        ``[]`` for a line with nothing serialized about it. For a KIT line this
        is the kit's serialized COMPONENTS — never the kit — because receiving
        a kit credits its components, and a serial written against the kit
        would name a unit that never enters stock.

        ``quantity`` is what the FULL ordered quantity implies; a partial
        receipt carries proportionally fewer. Rendered from
        :func:`reorder_queue.services.receiving.serialized_receipt_targets`,
        the same function the receipt validates the operator's serials against,
        so this can never offer an identity the receipt would then refuse.
        """
        from .services import serialized_receipt_targets

        return [
            {
                "item": str(item.pk),
                "item_name": item.name,
                "item_sku": item.sku,
                "serial_tracking_mode": item.serial_tracking_mode,
                "quantity": units,
            }
            for item, units in serialized_receipt_targets(obj, obj.quantity_ordered or 0)
        ]

    def get_serials_recorded(self, obj):
        """How many serialized units have been accessioned against this line so far.

        Counted from the units' own provenance link rather than tracked in a
        column, so it cannot drift from the units that actually exist. A client
        compares it with ``serial_targets`` to see what is still uncaptured, or
        reads ``serials_outstanding`` for that subtraction already done.
        """
        return obj.serialized_components.count()

    def get_serials_outstanding(self, obj):
        """Units credited to a serialized identity on this line with no serial yet.

        Zero when nothing on the line is serialized, and zero once every
        received unit has one — so a non-zero value always means real
        outstanding work, on a kit line and an ordinary line alike.
        """
        from .services import serials_outstanding

        return serials_outstanding(obj)

    def get_kit_components(self, obj):
        """What receiving this line's ordered quantity will credit (op-8n0).

        ``None`` — not ``[]`` — for every ordinary item, asset and freeform
        line, so the payload for a PO without kits stays byte-identical to what
        clients already parse.

        Rendered from the SAME generator the receipt applies
        (``inventory.services.kits.kit_component_credits``), reading the SAME
        ``kit_snapshot``, which is the whole reason that function is split out
        and side-effect-free: a preview computed a second way could disagree
        with what the receipt posts, and nobody would notice until the stock was
        wrong.

        The snapshot is also why editing the kit does not rewrite history here —
        this line shows the breakdown as ordered, which is what will arrive.
        """
        if not obj.is_kit_line:
            return None

        from inventory.services.kits import kit_component_credits

        return [
            {
                "component": credit.component.pk,
                "component_name": credit.component.name,
                "component_sku": credit.component.sku,
                "quantity_per_kit": credit.quantity_per_kit,
                "quantity": credit.quantity,
            }
            for credit in kit_component_credits(
                obj.item, obj.quantity_ordered or 0, snapshot=obj.kit_snapshot
            )
        ]

    def get_asset_details(self, obj):
        """Return asset details if this is an asset purchase."""
        # Route through the typed-target accessor (#884): obj.target is the Asset
        # when target_type == "asset".
        if obj.target_type == "asset":
            from inventory.serializers import AssetSerializer

            return AssetSerializer(obj.target).data
        return None

    def get_item_type(self, obj):
        """Return whether this is an inventory item, asset, or freeform."""
        # Backed by the typed-target accessor (#884): returns
        # inventory_item / asset / freeform (or None if nothing is set).
        return obj.target_type

    def get_work_order_details(self, obj):
        """Minimal identity of the work order this line was ordered for."""
        return work_order_identity(obj.work_order)

    def get_owning_group_details(self, obj):
        """Minimal identity of the committee this line was ordered for."""
        return owning_group_identity(obj.owning_group)


class PurchaseOrderAttachmentSerializer(serializers.ModelSerializer):
    """Serializer for files attached to a purchase order."""

    uploaded_by_name = serializers.SerializerMethodField()
    file_url = serializers.SerializerMethodField()
    file_name = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseOrderAttachment
        fields = [
            "id",
            "purchase_order",
            "file",
            "file_url",
            "file_name",
            "description",
            "uploaded_by",
            "uploaded_by_name",
            "uploaded_at",
        ]
        read_only_fields = [
            "purchase_order",
            "uploaded_at",
            "uploaded_by",
            "uploaded_by_name",
            "file_url",
            "file_name",
        ]

    def get_uploaded_by_name(self, obj):
        if obj.uploaded_by:
            return obj.uploaded_by.get_full_name() or obj.uploaded_by.username
        return None

    def get_file_url(self, obj):
        request = self.context.get("request")
        if obj.file and request:
            return request.build_absolute_uri(obj.file.url)
        if obj.file:
            return obj.file.url
        return None

    def get_file_name(self, obj):
        if obj.file:
            return obj.file.name.rsplit("/", 1)[-1]
        return None


def validate_agreement_supplier(attrs, instance=None):
    """An order may only cite an agreement held with *its own* supplier (op-yoos).

    Shared by the create and update serializers. Both sides of the pair are
    resolved from the payload *or*, on a partial update, from the instance —
    so re-pointing the order at a different supplier is caught just as surely
    as attaching the wrong agreement. To move an order to another supplier,
    clear or replace its agreement in the same request.

    Returns ``attrs`` unchanged; raises otherwise.
    """
    agreement = (
        attrs["supplier_agreement"]
        if "supplier_agreement" in attrs
        else getattr(instance, "supplier_agreement", None)
    )
    if agreement is None:
        return attrs

    supplier = attrs.get("supplier") or getattr(instance, "supplier", None)
    if supplier is not None and agreement.supplier_id != supplier.id:
        raise serializers.ValidationError(
            {
                "supplier_agreement": (
                    f"Agreement '{agreement.name}' belongs to "
                    f"{agreement.supplier.name}, not to the selected supplier."
                )
            }
        )
    return attrs


# How far ahead of "now" a hand-entered order date is still believable (op-bwo9).
# Backdating is unlimited — the field exists to record when an order was really
# placed — but a date a year out is a typo, not an intent.
MAX_ORDER_DATE_FUTURE_DAYS = 365


def validate_order_date_not_far_future(value):
    """Reject an order date more than a year ahead (op-bwo9).

    Shared by the create and update serializers so a backdated PO and a
    corrected one obey the same rule. Returns ``value`` unchanged; raises
    otherwise.
    """
    if value is None:
        return value
    horizon = timezone.now() + timedelta(days=MAX_ORDER_DATE_FUTURE_DAYS)
    if value > horizon:
        raise serializers.ValidationError(
            f"Order date is more than {MAX_ORDER_DATE_FUTURE_DAYS} days in the "
            "future — check the date."
        )
    return value


def render_payment_schedule(purchase_order):
    """JSON shape of ``PurchaseOrder.payment_schedule`` (op-bwo9).

    The model owns the math; this owns only the wire format — ``amount`` is
    stringified to two decimals so it matches the ``estimated_total`` it is
    derived from (DRF renders ``DecimalField`` as a string by default).
    """
    schedule = purchase_order.payment_schedule
    due_date = schedule["due_date"]
    return {
        "due_date": due_date.isoformat() if due_date else None,
        "amount": f"{schedule['amount']:.2f}",
        "basis": schedule["basis"],
    }


class PurchaseOrderSerializer(serializers.ModelSerializer):
    """Comprehensive serializer for purchase orders."""

    # Related data
    supplier_details = serializers.CharField(source="supplier.name", read_only=True)
    # Drives the adapter-aware order-pad affordances in the web UI (op-svpq): the
    # PurchaseOrderPage picks the Amazon "Open cart" vs HD Supply / generic
    # download+copy buttons from this without a second request.
    supplier_ordering_adapter = serializers.CharField(
        source="supplier.ordering_adapter", read_only=True, allow_null=True
    )
    # The purchase/pricing agreement this order was placed under (op-yoos).
    # Nested so the detail page can label it without a second request.
    supplier_agreement_details = serializers.SerializerMethodField()
    # Order-level associations (op-shb9). ``work_order``/``owning_group`` stay
    # writable — this serializer backs ``partial_update``/``update``, which is
    # how the detail page re-tags an order after it has gone out — and the
    # ``*_details`` blocks let a PO screen label them without a second fetch.
    work_order_details = serializers.SerializerMethodField()
    owning_group_details = serializers.SerializerMethodField()
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)
    sent_by_username = serializers.CharField(
        source="sent_by.username", read_only=True, allow_null=True
    )
    voided_by_username = serializers.CharField(
        source="voided_by.username", read_only=True, allow_null=True
    )

    # Line items
    items = PurchaseOrderItemSerializer(many=True, read_only=True)
    attachments = PurchaseOrderAttachmentSerializer(many=True, read_only=True)

    # Human-readable status, mirroring ``get_status_display``. The purchasing
    # list and detail screens render this directly; without it the status text
    # is blank in the UI even though the raw ``status`` drives the badge colour.
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    # Calculated fields
    total_items = serializers.IntegerField(read_only=True)
    total_quantity = serializers.IntegerField(read_only=True)
    total_received_quantity = serializers.IntegerField(read_only=True)
    is_fully_received = serializers.BooleanField(read_only=True)
    # Receiving roll-up (oms-po-receiving), all derived from the lines.
    # ``is_settled`` is "receiving is finished with this order" — which is what
    # advances it to ``received`` — while ``is_fully_received`` stays the
    # stricter "everything we ordered turned up". They differ exactly when a
    # line was closed short, and ``has_receipt_variance`` is what keeps that
    # difference visible after the order is closed.
    is_settled = serializers.BooleanField(read_only=True)
    has_receipt_variance = serializers.BooleanField(read_only=True)
    outstanding_line_count = serializers.IntegerField(read_only=True)
    variance_line_count = serializers.IntegerField(read_only=True)
    can_receive = serializers.SerializerMethodField()
    serials_outstanding = serializers.SerializerMethodField()
    days_since_ordered = serializers.IntegerField(read_only=True)
    estimated_total = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        source="effective_estimated_total",
        read_only=True,
    )
    # Derived single payment implied by ``payment_terms`` (op-bwo9). Read-only —
    # it is recomputed from the order's own fields on every read, never stored.
    payment_schedule = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseOrder
        fields = [
            "id",
            "po_number",
            "supplier",
            "supplier_details",
            "supplier_ordering_adapter",
            "supplier_agreement",
            "supplier_agreement_details",
            "work_order",
            "work_order_details",
            "owning_group",
            "owning_group_details",
            "status",
            "status_label",
            "priority",
            "payment_terms",
            "freight_terms",
            "order_date",
            "expected_delivery_date",
            "supplier_order_number",
            "sales_order_number",
            "notes",
            "estimated_total",
            "actual_total",
            "created_by",
            "created_by_username",
            "sent_by",
            "sent_by_username",
            "sent_at",
            "voided_at",
            "voided_by",
            "voided_by_username",
            "void_reason",
            "updated_at",
            # Related data
            "items",
            "attachments",
            # Calculated fields
            "total_items",
            "total_quantity",
            "total_received_quantity",
            "is_fully_received",
            "is_settled",
            "has_receipt_variance",
            "outstanding_line_count",
            "variance_line_count",
            "can_receive",
            "serials_outstanding",
            "days_since_ordered",
            "payment_schedule",
        ]
        # ``order_date`` is deliberately absent (op-bwo9): a PO entered after the
        # fact is backdated to when it was actually placed, so PATCH must reach it.
        read_only_fields = ["po_number", "updated_at"]

    def get_serials_outstanding(self, obj):
        """Units on this order sitting in stock with no serial recorded.

        Summed across the lines. Non-zero means somebody received goods —
        through any path, ``mark-delivered`` included — without capturing the
        serials, and the units are still identifiable only in aggregate.
        """
        from .services import serials_outstanding

        return sum(serials_outstanding(item) for item in obj.items.all())

    def get_can_receive(self, obj):
        """Whether this order's status allows receiving against it.

        Served from ``PurchaseOrder.RECEIVABLE_STATUSES`` so a client never has
        to keep its own copy of which statuses those are — the copy the web UI
        used to keep is how the receive button and the endpoint could disagree
        about whether an order was receivable.
        """
        return obj.status in PurchaseOrder.RECEIVABLE_STATUSES

    def validate(self, attrs):
        """Re-attaching an agreement must respect the order's supplier."""
        return validate_agreement_supplier(super().validate(attrs), self.instance)

    def validate_order_date(self, value):
        """Reject an order date implausibly far in the future (op-bwo9).

        Backdating is the point of the field, and same-day/near-future entry is
        legitimate, so only a date beyond a generous horizon is treated as a
        typo rather than an intent.
        """
        return validate_order_date_not_far_future(value)

    def get_payment_schedule(self, obj):
        """``{due_date, amount, basis}`` — see ``PurchaseOrder.payment_schedule``."""
        return render_payment_schedule(obj)

    def get_supplier_agreement_details(self, obj):
        """Minimal {id, name} for the attached agreement, or None."""
        agreement = obj.supplier_agreement
        if agreement is None:
            return None
        return {"id": agreement.id, "name": agreement.name}

    def get_work_order_details(self, obj):
        """Minimal identity of the work order this order was placed for."""
        return work_order_identity(obj.work_order)

    def get_owning_group_details(self, obj):
        """Minimal identity of the committee this order was placed for."""
        return owning_group_identity(obj.owning_group)


class PurchaseOrderCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating purchase orders with line items."""

    items = serializers.ListField(
        child=serializers.DictField(),
        write_only=True,
        help_text="List of items to order. Supports three formats: "
        '[{"item_supplier_id": 1, "quantity": 10, "unit_cost": 5.00, "expected_shipment_date": "2024-01-15"}, ...] for inventory items '
        "(unit_cost and expected_shipment_date are optional), "
        '[{"asset_id": "uuid", "quantity": 1, "unit_cost": 100.00}, ...] for assets, '
        '[{"description": "Custom item", "quantity": 1, "unit_cost": 50.00}, ...] for freeform items. '
        'Any line may also carry "work_order_id": "uuid" to record that it was ordered '
        "to complete that work order; receiving it posts the material and its cost "
        'onto that work order. A line may also carry "owning_group_id": 3 to attribute '
        "it to a committee (SIG); that is metadata only and moves no money.",
    )

    class Meta:
        model = PurchaseOrder
        fields = [
            "id",
            "po_number",
            "supplier",
            "supplier_agreement",
            # Order-level associations (op-shb9), both optional. Per-line
            # associations ride in the ``items`` dicts above.
            "work_order",
            "owning_group",
            # Header details (op-bwo9), all optional: ``order_date`` backdates an
            # order entered after it was placed; the other three fall back to
            # their model defaults (normal priority, no terms agreed yet).
            "order_date",
            "priority",
            "payment_terms",
            "freight_terms",
            "expected_delivery_date",
            "sales_order_number",
            "notes",
            "items",
        ]
        read_only_fields = ["id", "po_number"]

    def validate(self, attrs):
        """Reject an agreement that belongs to a different supplier (op-yoos)."""
        return validate_agreement_supplier(super().validate(attrs))

    def validate_order_date(self, value):
        """Reject an order date implausibly far in the future (op-bwo9)."""
        return validate_order_date_not_far_future(value)

    def validate_items(self, value):
        """Validate items list: non-empty and no duplicate item_supplier_id/asset_id."""
        if not value or len(value) == 0:
            raise serializers.ValidationError(
                "At least one item is required to create a purchase order."
            )

        seen_item_suppliers = set()
        seen_assets = set()
        for item in value:
            if not isinstance(item, dict):
                continue
            if "item_supplier_id" in item:
                key = item["item_supplier_id"]
                if key in seen_item_suppliers:
                    raise serializers.ValidationError(
                        f"Item supplier {key} appears more than once. "
                        "Combine the quantities into a single line."
                    )
                seen_item_suppliers.add(key)
            if "asset_id" in item:
                key = item["asset_id"]
                if key in seen_assets:
                    raise serializers.ValidationError(
                        f"Asset {key} appears more than once. "
                        "Combine the quantities into a single line."
                    )
                seen_assets.add(key)
        return value

    def to_representation(self, instance):
        """Return full purchase order details after creation."""
        return PurchaseOrderSerializer(instance, context=self.context).data

    def create(self, validated_data):
        """Create purchase order with line items (inventory items, assets, or freeform).

        The PO/line-item creation workflow lives in
        :func:`reorder_queue.services.create_purchase_order` (#883); this
        serializer stays the request/response boundary and owns input
        validation.
        """
        items_data = validated_data.pop("items")

        # Validate items list is not empty
        if not items_data or len(items_data) == 0:
            raise serializers.ValidationError(
                {"items": "At least one item is required to create a purchase order."}
            )

        # Ensure request context is available
        if "request" not in self.context:
            raise serializers.ValidationError("Request context is missing.")

        user = self.context["request"].user
        if not user or not user.is_authenticated:
            raise serializers.ValidationError(
                "User must be authenticated to create a purchase order."
            )

        return create_purchase_order(validated_data, items_data, user)


# Delivery and Receipt Serializers


class DeliveryItemSerializer(serializers.ModelSerializer):
    """Serializer for individual items received in a delivery."""

    # Related data
    item_details = InventoryItemSerializer(source="item", read_only=True)
    supplier_details = serializers.CharField(source="supplier.name", read_only=True)
    scanned_by_username = serializers.CharField(
        source="scanned_by.username", read_only=True, allow_null=True
    )

    class Meta:
        model = DeliveryItem
        fields = [
            "id",
            "delivery",
            "purchase_order_item",
            "item_details",
            "supplier_details",
            "quantity_received",
            "is_damaged",
            "is_expired",
            "condition_notes",
            "scanned_upc",
            "scanned_at",
            "scanned_by",
            "scanned_by_username",
            "created_at",
        ]
        read_only_fields = ["created_at"]


class OrderDeliverySerializer(serializers.ModelSerializer):
    """Serializer for order deliveries/receipts."""

    # Related data
    purchase_order_details = PurchaseOrderSerializer(source="purchase_order", read_only=True)
    received_by_username = serializers.CharField(source="received_by.username", read_only=True)

    # Items received in this delivery
    items = DeliveryItemSerializer(many=True, read_only=True)

    # Calculated fields
    total_items_received = serializers.IntegerField(read_only=True)
    total_quantity_received = serializers.IntegerField(read_only=True)

    class Meta:
        model = OrderDelivery
        fields = [
            "id",
            "purchase_order",
            "purchase_order_details",
            "delivery_date",
            "tracking_number",
            "carrier",
            "received_by",
            "received_by_username",
            "receipt_notes",
            "is_complete",
            "items",
            "total_items_received",
            "total_quantity_received",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]


class BarcodeReceiptSerializer(serializers.Serializer):
    """Serializer for barcode scanning during order receipt."""

    purchase_order_id = serializers.IntegerField()
    scanned_upc = serializers.CharField(max_length=50)
    quantity_received = serializers.IntegerField(min_value=1)
    is_damaged = serializers.BooleanField(default=False)
    is_expired = serializers.BooleanField(default=False)
    condition_notes = serializers.CharField(max_length=500, required=False, allow_blank=True)

    def validate_purchase_order_id(self, value):
        """Validate that the purchase order exists and is in the correct state."""
        try:
            po = PurchaseOrder.objects.get(id=value)
            if po.status not in [
                PurchaseOrder.Status.SENT,
                PurchaseOrder.Status.CONFIRMED,
                PurchaseOrder.Status.PARTIALLY_RECEIVED,
            ]:
                raise serializers.ValidationError(
                    "Purchase order must be sent, confirmed, or partially received to accept deliveries"
                )
            return value
        except PurchaseOrder.DoesNotExist:
            raise serializers.ValidationError("Purchase order does not exist")


class MarkDeliveredSerializer(serializers.Serializer):
    """Serializer for manually marking a purchase order as delivered."""

    delivery_date = serializers.DateField()
    tracking_number = serializers.CharField(
        max_length=100, required=False, allow_blank=True, default=""
    )
    carrier = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    receipt_notes = serializers.CharField(required=False, allow_blank=True, default="")


class ReceiveSerialSerializer(serializers.Serializer):
    """One serial-numbered unit captured during a receipt.

    ``item`` names which inventory identity the serial belongs to. It is
    OPTIONAL on an ordinary line (there is only one identity it could be) and
    REQUIRED on a kit line, where the receipt credits several components and
    guessing between them would be inventing the operator's intent. Naming the
    kit itself is refused — see
    :func:`reorder_queue.services.receiving.resolve_serial_targets`.

    ``lot`` and ``expiration_date`` are optional provenance recorded verbatim
    against the unit; neither affects stock or triggers any alert.
    """

    serial_number = serializers.CharField(max_length=200)
    item = serializers.UUIDField(required=False, allow_null=True)
    lot = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    expiration_date = serializers.DateField(required=False, allow_null=True)

    def validate_serial_number(self, value):
        """A serial is the barcode as scanned, minus the whitespace a scanner adds."""
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError("A serial number cannot be blank.")
        return cleaned


class ReceiveItemSerializer(serializers.Serializer):
    """A single line in a per-item purchase order receive request.

    ``quantity_received`` is BASE units unless ``at_level`` is set, in which
    case it is a count of whole packs of the item's ``count_level`` ("three
    cases came in") and is converted before anything else (op-ev14).
    ``at_level`` on a line whose item is not counted in packs, or on an
    asset/freeform line, is an error rather than a silent base-unit reading.

    **More than the outstanding quantity is accepted.** An over-receipt is
    recorded as the figure that actually arrived and flagged on the line; it is
    never rounded down to what was ordered.

    ``serials`` carries the units of a serialized item (or, on a kit line, of
    its serialized components) that came in with this quantity. ``close_short``
    declares that whatever is still outstanding after this receipt is never
    arriving, which settles the line so the order can finish.
    """

    purchase_order_item = serializers.IntegerField()
    quantity_received = serializers.IntegerField(min_value=1)
    at_level = serializers.BooleanField(required=False, default=False)
    serials = ReceiveSerialSerializer(many=True, required=False, default=list)
    close_short = serializers.BooleanField(required=False, default=False)
    close_short_reason = serializers.CharField(required=False, allow_blank=True, default="")


class ReceiveItemsSerializer(serializers.Serializer):
    """Serializer for receiving specific PO line items with explicit quantities.

    Unlike :class:`MarkDeliveredSerializer` (which receives every pending
    quantity on the whole PO), this drives a partial receipt from an explicit
    list of ``{purchase_order_item, quantity_received}`` lines. ``delivery_date``
    is optional and defaults to now when omitted.

    ``tracking_number`` is the carrier's tracking barcode for the parcel this
    receipt covers, stored exactly as scanned. It is recorded, never parsed:
    together with the delivery's ``received_at`` it is what a transit-duration
    calculation would later be built from.
    """

    items = ReceiveItemSerializer(many=True, allow_empty=False)
    delivery_date = serializers.DateField(required=False)
    tracking_number = serializers.CharField(
        max_length=100, required=False, allow_blank=True, default=""
    )
    carrier = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    receipt_notes = serializers.CharField(required=False, allow_blank=True, default="")


class LineSettlementSerializer(serializers.Serializer):
    """One line named by a settlement action, with the operator's reason for it.

    Shared by ``close-short`` and ``reopen-short`` because the two are the same
    shape: name a line, say why. Recording the reason to the same standard on
    both sides is what lets the pair read as a mistake and its correction.
    """

    purchase_order_item = serializers.IntegerField()
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class CloseShortSerializer(serializers.Serializer):
    """Request body for writing off outstanding balances on named lines."""

    items = LineSettlementSerializer(many=True, allow_empty=False)


class ReopenShortSerializer(serializers.Serializer):
    """Request body for taking back the close-short on named lines.

    ``reason`` is why the write-off is being corrected, and is stamped on the
    line beside the close-short it corrects rather than replacing it.
    """

    items = LineSettlementSerializer(many=True, allow_empty=False)


class MarkReceivedSerializer(serializers.Serializer):
    """Request body for finishing an order off (step 6 of the receiving flow).

    ``reason`` is recorded against every line that still had an outstanding
    balance when the order was closed, so a shortfall written off in bulk is as
    traceable as one written off line by line.
    """

    reason = serializers.CharField(required=False, allow_blank=True, default="")


def ordered_unit_cost_field(**kwargs):
    """The one definition of a valid ``unit_cost_ordered`` on the wire.

    Both accept-points for that figure — adding a line and repricing one — take
    the same money against the same ``decimal(10, 4)`` column, so they validate
    through this single field rather than two hand-written checks that can drift
    apart. It is what rejects NaN, an infinity, and an extra-zeros fat-finger
    too wide for the column with a 400 rather than a 500.
    """
    return serializers.DecimalField(
        max_digits=10, decimal_places=4, min_value=Decimal("0"), **kwargs
    )


class RepricePurchaseOrderLineSerializer(serializers.Serializer):
    """The ``unit_cost_ordered`` half of a line-item PATCH.

    Only that one key: the rest of ``update_item``'s body is read field by field
    against rules of its own, and this exists so a reprice is validated exactly
    as ``AddPurchaseOrderLineSerializer.unit_cost`` validates the same figure.
    """

    unit_cost_ordered = ordered_unit_cost_field()


class AddPurchaseOrderLineSerializer(serializers.Serializer):
    """Request body for adding a line to a **draft** purchase order (oms-po-add-item).

    Exactly **one** of the four ways to name what is being bought is required.
    They are the three line shapes ``PurchaseOrderItem`` supports, with the
    inventory shape offered two ways:

    * ``identifier`` — inventory. What the operator typed or the scanner
      emitted: the item's name, the item's own SKU, a package or unit barcode,
      or the vendor's SKU. Resolved against what the order's supplier supplies
      (see :mod:`reorder_queue.services.line_entry`).
    * ``item_supplier`` — inventory. The exact catalogue row, used when the
      operator has already picked one out of an ambiguous lookup.
    * ``asset`` — a tracked hard asset being purchased, by id.
    * ``description`` — a freeform line for something the catalogue does not
      know about.

    On the inventory shapes everything else is optional and has a sensible
    default derived from the supplier relationship and this item's purchase
    history, so a bare scan produces a fully-formed line. The asset and freeform
    shapes have no such relationship to derive from, so ``unit_cost`` is
    **required** on both — the same rule ``create_purchase_order`` applies when
    an order is created with those lines, rather than a laxer one that would
    make a line cheaper to add late than to order up front.
    """

    identifier = serializers.CharField(required=False, allow_blank=False, trim_whitespace=True)
    item_supplier = serializers.IntegerField(required=False)
    asset = serializers.UUIDField(required=False)
    description = serializers.CharField(
        required=False, allow_blank=False, trim_whitespace=True, max_length=500
    )
    quantity = serializers.IntegerField(required=False, min_value=1)
    unit_cost = ordered_unit_cost_field(required=False)
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    work_order = serializers.UUIDField(required=False, allow_null=True)
    owning_group = serializers.IntegerField(required=False, allow_null=True)

    #: Request key → the line shape it selects. Ordered as the docstring lists
    #: them so the error message reads the same way.
    SHAPE_FIELDS = ("identifier", "item_supplier", "asset", "description")
    #: Shapes with nothing to derive a price from, and what to call each in a
    #: refusal — "a description line" is not a phrase an operator would use.
    COST_REQUIRED_SHAPES = {"asset": "an asset", "description": "a freeform"}

    def validate(self, attrs):
        supplied = [name for name in self.SHAPE_FIELDS if attrs.get(name) is not None]
        if len(supplied) != 1:
            raise serializers.ValidationError(
                "Name what to add exactly one way: 'identifier' (a typed or "
                "scanned name, SKU, barcode, or supplier SKU), 'item_supplier' "
                "(a catalogue row picked out of an ambiguous lookup), 'asset', "
                f"or 'description' (a freeform line). Got: {supplied or 'none'}."
            )

        label = self.COST_REQUIRED_SHAPES.get(supplied[0])
        if label is not None and attrs.get("unit_cost") is None:
            raise serializers.ValidationError(
                {
                    "unit_cost": (
                        f"unit_cost is required on {label} line — there is no "
                        "supplier relationship or purchase history to price it from."
                    )
                }
            )
        return attrs


# Lead Time and Analytics Serializers


class LeadTimeLogSerializer(serializers.ModelSerializer):
    """Serializer for lead time tracking data."""

    # Related data
    item_details = InventoryItemSerializer(source="item", read_only=True)
    supplier_details = serializers.CharField(source="supplier.name", read_only=True)
    purchase_order_details = serializers.CharField(
        source="purchase_order.po_number", read_only=True
    )

    # Calculated fields
    was_late = serializers.BooleanField(read_only=True)
    was_early = serializers.BooleanField(read_only=True)

    class Meta:
        model = LeadTimeLog
        fields = [
            "id",
            "item_supplier",
            "item_details",
            "supplier_details",
            "purchase_order",
            "purchase_order_details",
            "order_date",
            "expected_delivery_date",
            "actual_delivery_date",
            "estimated_lead_time_days",
            "actual_lead_time_days",
            "variance_days",
            "quantity_ordered",
            "quantity_received",
            "was_late",
            "was_early",
            "recorded_at",
        ]
        read_only_fields = ["recorded_at"]


# Dashboard and Analytics Serializers


class OrderMetricsSerializer(serializers.Serializer):
    """Serializer for order dashboard metrics."""

    # Order counts
    total_orders = serializers.IntegerField()
    draft_orders = serializers.IntegerField()
    sent_orders = serializers.IntegerField()
    confirmed_orders = serializers.IntegerField()
    partially_received_orders = serializers.IntegerField()
    completed_orders = serializers.IntegerField()

    # Item metrics
    total_items_on_order = serializers.IntegerField()
    total_items_received = serializers.IntegerField()
    items_pending_receipt = serializers.IntegerField()

    # Financial metrics
    total_order_value = serializers.DecimalField(max_digits=15, decimal_places=2)
    received_order_value = serializers.DecimalField(max_digits=15, decimal_places=2)
    pending_order_value = serializers.DecimalField(max_digits=15, decimal_places=2)

    # Recent activity
    orders_created_this_week = serializers.IntegerField()
    orders_received_this_week = serializers.IntegerField()

    # Lead time metrics
    average_lead_time_days = serializers.FloatField()
    on_time_delivery_rate = serializers.FloatField()  # Percentage


class SupplierPerformanceSerializer(serializers.Serializer):
    """Serializer for supplier performance metrics."""

    supplier_id = serializers.IntegerField()
    supplier_name = serializers.CharField()

    # Order metrics
    total_orders = serializers.IntegerField()
    completed_orders = serializers.IntegerField()
    active_orders = serializers.IntegerField()

    # Delivery performance
    average_lead_time_days = serializers.FloatField()
    on_time_delivery_rate = serializers.FloatField()
    early_delivery_rate = serializers.FloatField()
    late_delivery_rate = serializers.FloatField()

    # Financial metrics
    total_order_value = serializers.DecimalField(max_digits=15, decimal_places=2)

    # Quality metrics
    damage_rate = serializers.FloatField()  # Percentage of damaged items

    # Recent activity
    last_order_date = serializers.DateTimeField(allow_null=True)
    days_since_last_order = serializers.IntegerField(allow_null=True)


# WebHook Serializers


class WebHookSerializer(serializers.ModelSerializer):
    """Serializer for webhook configurations."""

    # Display fields
    event_type_display = serializers.CharField(source="get_event_type_display", read_only=True)
    success_rate = serializers.SerializerMethodField()
    total_triggers = serializers.SerializerMethodField()

    class Meta:
        model = WebHook
        fields = [
            "id",
            "name",
            "description",
            "url",
            "event_type",
            "event_type_display",
            "is_active",
            "secret",
            "headers",
            "last_triggered_at",
            "success_count",
            "failure_count",
            "success_rate",
            "total_triggers",
            "last_error",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "last_triggered_at",
            "success_count",
            "failure_count",
            "last_error",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "secret": {"write_only": True},  # Don't expose secret in responses
        }

    def get_success_rate(self, obj):
        """Calculate success rate percentage."""
        total = obj.success_count + obj.failure_count
        if total == 0:
            return None
        return round((obj.success_count / total) * 100, 2)

    def get_total_triggers(self, obj):
        """Get total number of webhook triggers."""
        return obj.success_count + obj.failure_count


class WebHookCreateSerializer(serializers.ModelSerializer):
    """Simplified serializer for creating webhooks."""

    class Meta:
        model = WebHook
        fields = [
            "name",
            "description",
            "url",
            "event_type",
            "is_active",
            "secret",
            "headers",
        ]
        extra_kwargs = {
            "description": {"required": False},
            "is_active": {"required": False},
            "secret": {"required": False},
            "headers": {"required": False},
        }


class WebHookTestResultSerializer(serializers.Serializer):
    """Serializer for webhook test results."""

    webhook_id = serializers.IntegerField(required=False, allow_null=True)
    webhook_name = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    task_id = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    task_status = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    success = serializers.BooleanField(required=False, allow_null=True)
    status_code = serializers.IntegerField(required=False, allow_null=True)
    response_time_ms = serializers.FloatField(required=False, allow_null=True)
    error_message = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    response_body = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    tested_at = serializers.DateTimeField(required=False, allow_null=True)
