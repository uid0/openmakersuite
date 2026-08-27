"""
Models for reorder queue management.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import IntegrityError, models, transaction
from django.db.models import Case, F, Q, Value, When
from django.db.models.functions import Greatest
from django.utils import timezone
from django.utils.functional import cached_property

from config.observability_redaction import redact
from inventory.models import InventoryItem, ItemSupplier, Supplier, TargetField, TypedTargetModel

User = get_user_model()


class ReorderRequest(models.Model):
    """
    Tracks requests to reorder items.

    Created when users scan QR codes and request reorders.
    Manages the full lifecycle from request to delivery:
    - Pending: Initial request submitted
    - Approved: Admin has approved the request
    - Ordered: Order placed with supplier
    - Received: Items delivered and stocked
    - Cancelled: Request cancelled
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        ORDERED = "ordered", "Ordered"
        RECEIVED = "received", "Received"
        CANCELLED = "cancelled", "Cancelled"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    item = models.ForeignKey(
        InventoryItem, on_delete=models.CASCADE, related_name="reorder_requests"
    )
    quantity = models.PositiveIntegerField(help_text="Quantity requested to reorder")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    priority = models.CharField(max_length=20, choices=Priority.choices, default=Priority.NORMAL)

    # Request information
    requested_by = models.CharField(
        max_length=100, blank=True, help_text="Name or ID of person requesting reorder"
    )
    request_notes = models.TextField(blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)

    # Admin handling
    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_reorders",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    admin_notes = models.TextField(blank=True)

    # Order tracking
    ordered_at = models.DateTimeField(null=True, blank=True)
    estimated_delivery = models.DateField(null=True, blank=True)
    actual_delivery = models.DateField(null=True, blank=True)
    order_number = models.CharField(max_length=100, blank=True)
    actual_cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # Transparency tracking (public information)
    invoice_number = models.CharField(
        max_length=100, blank=True, help_text="Invoice/receipt number for transparency"
    )
    invoice_url = models.URLField(
        blank=True, help_text="Link to invoice/receipt (if publicly available)"
    )
    purchase_order_url = models.URLField(blank=True, help_text="Link to purchase order document")
    delivery_tracking_url = models.URLField(
        blank=True, help_text="Link to shipping/delivery tracking"
    )
    supplier_url = models.URLField(blank=True, help_text="Link to supplier item page")
    public_notes = models.TextField(
        blank=True, help_text="Public notes visible in transparency view"
    )

    # Metadata
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-requested_at"]
        indexes = [
            models.Index(fields=["status", "-requested_at"]),
            models.Index(fields=["item", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.item.name} - {self.quantity} units ({self.status})"

    @property
    def estimated_cost(self) -> Optional[Decimal]:
        """Calculate estimated cost based on item unit cost."""
        # Read the item's primary unit cost once: ``item.unit_cost`` resolves the
        # primary supplier, so reading it twice used to double the (now cached,
        # prefetch-friendly) lookup on every row of a reorder list (issue #882).
        unit_cost = self.item.unit_cost
        if unit_cost:
            return self.quantity * unit_cost
        return None

    @property
    def days_pending(self) -> int:
        """Calculate how many days the request has been pending."""
        if self.status == self.Status.PENDING and self.requested_at:
            return (timezone.now() - self.requested_at).days
        return 0

    @property
    def has_transparency_data(self) -> bool:
        """Check if this request has financial transparency information to display."""
        return bool(
            self.actual_cost
            or self.invoice_number
            or self.invoice_url
            or self.purchase_order_url
            or self.delivery_tracking_url
            or self.order_number
        )

    @property
    def cost_per_unit(self) -> Optional[Decimal]:
        """Calculate cost per unit if actual cost is available."""
        if self.actual_cost and self.quantity > 0:
            return self.actual_cost / self.quantity
        return None


def outstanding_of(items) -> list:
    """The lines receiving is still waiting on, out of ``items``.

    THE derivation of "which lines does receiving still owe?", written once and
    reached through :attr:`PurchaseOrder.outstanding_items` (and its service
    alias ``reorder_queue.services.outstanding_lines``). Every site that has to
    act on those lines — the order's settlement roll-up, ``mark-delivered``,
    ``mark-received``, the receiving worksheet — comes through one of those two
    names, so none of them can drift onto a predicate of its own.

    Built on each line's :attr:`PurchaseOrderItem.is_settled`, so a line that
    becomes settled by a route added later drops out of here without this
    function being touched — and a voided line, or one closed short, is never
    handed back as work still to do.

    Takes an iterable rather than an order so the aggregate pass can hand it the
    lines it has already materialised instead of fetching them twice. Code that
    has a queryset instead of loaded lines asks
    :meth:`PurchaseOrderItemQuerySet.outstanding` — the same question, one
    derivation further down.
    """
    return [item for item in items if not item.is_settled]


class PurchaseOrder(models.Model):
    """
    Purchase order placed with a supplier.

    Groups multiple items from the same supplier into a single order.
    Tracks the complete lifecycle from creation to delivery.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SENT = "sent", "Sent to Supplier"
        CONFIRMED = "confirmed", "Confirmed by Supplier"
        PARTIALLY_RECEIVED = "partially_received", "Partially Received"
        RECEIVED = "received", "Fully Received"
        CANCELLED = "cancelled", "Cancelled"
        VOIDED = "voided", "Voided"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    class PaymentTerms(models.TextChoices):
        """When the supplier expects to be paid. Blank == not agreed yet."""

        DUE_ON_RECEIPT = "due_on_receipt", "Due on Receipt"
        NET_15 = "net_15", "Net 15"
        NET_30 = "net_30", "Net 30"
        NET_60 = "net_60", "Net 60"
        COD = "cod", "Cash on Delivery"
        PREPAID = "prepaid", "Prepaid"

    class FreightTerms(models.TextChoices):
        """Who pays the freight (and from where). Blank == not agreed yet."""

        FOB_ORIGIN = "fob_origin", "FOB Origin"
        FOB_DESTINATION = "fob_destination", "FOB Destination"
        PREPAID = "prepaid", "Prepaid"
        COLLECT = "collect", "Collect"
        THIRD_PARTY = "third_party", "Third Party"

    #: Statuses an order can be received against — the order is in flight with
    #: the supplier and not struck off. The ONE definition: the ``receive``,
    #: ``mark-delivered``, ``close-short`` and ``mark-received`` actions all
    #: gate on this, the receiving worksheet reports it, and the web UI reads it
    #: off the API rather than keeping a fourth copy of the same list.
    RECEIVABLE_STATUSES = frozenset(
        {
            Status.SENT,
            Status.CONFIRMED,
            Status.PARTIALLY_RECEIVED,
        }
    )

    #: Statuses in which the order has NOT yet gone to the supplier — it is
    #: still the shop's OWN document, so its line set is still the shop's to
    #: change. The ONE definition of that boundary: ``assert_addable`` and
    #: ``assert_deletable`` both gate on this, and the web UI reads the answer
    #: off the API (``can_add_items`` / ``can_delete_items``) rather than
    #: keeping its own copy of which statuses those are.
    #:
    #: The boundary is not the *label* ``draft`` — it is whether the supplier
    #: has seen the document. That is why adding a second pre-send state (an
    #: approval hold, say) is one edit HERE and the add and delete guards both
    #: follow it, rather than two hard-coded status comparisons to find.
    #:
    #: CANCELLED and VOIDED are deliberately absent even though a *draft* can
    #: reach both. They are terminal: the order is closed and its lines are a
    #: record of what was closed, not a working set. "Nobody has seen it yet"
    #: and "nobody is working on it any more" are different facts, and only the
    #: first licenses editing.
    #:
    #: Disjoint from :attr:`IN_RECEIVING_STATUSES` by construction — a status
    #: cannot be both un-sent and in receiving — and ``test_line_delete``
    #: asserts that rather than trusting it.
    PRE_SUPPLIER_STATUSES = frozenset({Status.DRAFT})

    #: Statuses in which receiving still owns the order: the receivable ones
    #: plus RECEIVED, which receiving can still be corrected *out of*. Reopening
    #: a line closed short in error is exactly that correction, and
    #: ``refresh_receipt_status`` uses the same set to decide which orders it
    #: may re-derive — a draft or cancelled order is never resurrected by one.
    IN_RECEIVING_STATUSES = RECEIVABLE_STATUSES | {Status.RECEIVED}

    # Days-until-due for the "net N" terms. Every other term anchors the payment
    # to a date rather than to a delay — see :attr:`payment_schedule`.
    NET_PAYMENT_DAYS = {
        PaymentTerms.NET_15: 15,
        PaymentTerms.NET_30: 30,
        PaymentTerms.NET_60: 60,
    }

    # Core fields
    po_number = models.CharField(
        max_length=50,
        unique=True,
        blank=True,
        null=True,
        help_text="Purchase Order Number",
    )
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name="purchase_orders")
    # Which purchase/pricing agreement this order was placed under (op-yoos).
    # Optional — most orders are placed at list price.
    supplier_agreement = models.ForeignKey(
        "inventory.SupplierAgreement",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_orders",
        help_text="Purchase/pricing agreement this order was placed under, if any",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)

    # Order-level association (op-shb9). "This whole order was placed for job X"
    # / "…on behalf of committee Y". Both are attribution metadata: the
    # PO -> work-order material bridge stays line-level
    # (``PurchaseOrderItem.work_order``) and receiving still books the committee
    # from the received item's own ``owning_group``, so nothing here moves stock
    # or posts to the ledger.
    work_order = models.ForeignKey(
        "inventory.WorkOrder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_orders",
        help_text="Work order this whole order was placed for (attribution only)",
    )
    # Committee == the owning SIG, mirroring ``inventory.OwnableModel``'s
    # ``owning_group``. Deliberately a standalone field rather than inheriting
    # OwnableModel: an order is not user- or space-owned, so pulling in
    # ownership_type/owning_user would add two columns nothing reads.
    owning_group = models.ForeignKey(
        Group,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reorder_purchaseorder_owned",
        help_text="Committee (SIG) this order was placed on behalf of",
    )

    # Order details
    # User-editable (op-bwo9): defaults to "now" but a PO entered after the
    # fact can be backdated to when the order was actually placed. Deliberately
    # NOT auto_now_add — that made the field read-only at every layer.
    order_date = models.DateTimeField(
        default=timezone.now,
        help_text="When the order was actually placed (may be backdated)",
    )
    expected_delivery_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    # Header metadata (op-bwo9). All three are plain descriptive terms: nothing
    # here moves stock or posts to the ledger. ``payment_terms`` additionally
    # drives the derived :attr:`payment_schedule` below.
    priority = models.CharField(
        max_length=20,
        choices=Priority.choices,
        default=Priority.NORMAL,
        help_text="How urgently this order needs to be placed/filled",
    )
    payment_terms = models.CharField(
        max_length=20,
        choices=PaymentTerms.choices,
        blank=True,
        help_text="Payment terms agreed with the supplier",
    )
    freight_terms = models.CharField(
        max_length=20,
        choices=FreightTerms.choices,
        blank=True,
        help_text="Freight terms — who pays to ship this order",
    )

    # Supplier-side reference numbers (filled in after creation, e.g. once the
    # supplier confirms the order and assigns their own identifiers)
    supplier_order_number = models.CharField(
        max_length=128,
        blank=True,
        help_text="Order number assigned by the supplier (e.g. their internal order ID)",
    )
    sales_order_number = models.CharField(
        max_length=128,
        blank=True,
        help_text="Sales order number associated with this purchase order",
    )

    # Financial tracking
    estimated_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Estimated total cost of the order",
    )
    actual_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Actual total cost after delivery",
    )

    # User tracking
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="created_orders")
    sent_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_orders",
    )
    sent_at = models.DateTimeField(null=True, blank=True)

    # Void tracking (for orphaned/rejected POs)
    voided_at = models.DateTimeField(
        null=True, blank=True, help_text="When this purchase order was voided"
    )
    voided_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="voided_purchase_orders",
        help_text="User who voided this purchase order",
    )
    void_reason = models.TextField(blank=True, help_text="Reason for voiding this purchase order")

    # Metadata
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-order_date"]
        indexes = [
            models.Index(fields=["supplier", "status"]),
            models.Index(fields=["status", "-order_date"]),
            models.Index(fields=["po_number"]),
        ]

    def __str__(self) -> str:
        return f"PO #{self.po_number} - {self.supplier.name} ({self.status})"

    @cached_property
    def _line_item_totals(self) -> dict:
        """Compute all PO-level line-item aggregates in a single pass (#883).

        The list serializer, ``pending_orders``, and the admin changelist read
        several of the calculated fields below; deriving them together here (and
        caching per instance) collapses what were 5-6 separate loops over the
        prefetched line items into one. Values are identical to the standalone
        properties they back, including the voided-line exclusion.

        Cached per instance: the receive/void workflows read these aggregates
        only AFTER their line-item mutations (never before), so a stale
        pre-mutation read cannot occur — matching prior behaviour.
        """
        active_count = 0
        total_quantity = 0
        total_received_quantity = 0
        voided_estimated_total = Decimal("0.00")
        all_fully_received = True
        variance_count = 0
        items = list(self.items.all())
        for item in items:
            # total_received_quantity counts every line, voided or not.
            if item.quantity_received is not None:
                total_received_quantity += item.quantity_received
            if item.is_voided:
                voided_estimated_total += item.estimated_cost
                # A voided line is settled: it was struck off the order, so
                # nothing is coming and nothing should block the order
                # finishing. It used to be counted in ``is_fully_received``,
                # which left every order carrying a voided line stuck at
                # ``partially_received`` for ever.
                continue
            active_count += 1
            if item.quantity_ordered is not None:
                total_quantity += item.quantity_ordered
            if not item.is_fully_received:
                all_fully_received = False
            if item.is_settled and item.has_receipt_variance:
                variance_count += 1
        # "Is receiving finished?" and "how many lines is it still waiting on?"
        # are the same question as "which lines?", so all three come off the one
        # derivation rather than being counted by a second predicate here. Two
        # predicates answering it is how mark-delivered came to re-receive lines
        # this roll-up already considered settled.
        outstanding = outstanding_of(items)
        return {
            "total_items": active_count,
            "total_quantity": total_quantity,
            "total_received_quantity": total_received_quantity,
            "voided_estimated_total": voided_estimated_total,
            # ``all()`` over zero active lines is vacuously true, and "everything
            # arrived" is not a true thing to say about an order whose lines were
            # every one struck off — or about one that never had a line. The
            # emptiness is checked before the claim is made.
            "is_fully_received": active_count > 0 and all_fully_received,
            "is_settled": not outstanding,
            "outstanding_line_count": len(outstanding),
            "variance_line_count": variance_count,
        }

    @property
    def outstanding_items(self) -> list["PurchaseOrderItem"]:
        """This order's lines that receiving is still waiting on, in its own order.

        :func:`outstanding_of` applied to this order's lines — see it for why
        there is only one of these.
        """
        return outstanding_of(self.items.all())

    @property
    def total_items(self) -> int:
        """Total number of distinct non-voided items in this order."""
        return self._line_item_totals["total_items"]

    @property
    def total_quantity(self) -> int:
        """Total quantity of non-voided items ordered."""
        return self._line_item_totals["total_quantity"]

    @property
    def effective_estimated_total(self) -> Decimal:
        """Estimated total cost excluding voided line items.

        Voided lines are subtracted from the stored ``estimated_total`` so the
        displayed cost reflects only items the supplier is actually fulfilling.
        """
        base = self.estimated_total or Decimal("0.00")
        adjusted = base - self._line_item_totals["voided_estimated_total"]
        if adjusted < Decimal("0.00"):
            return Decimal("0.00")
        return adjusted

    @property
    def payment_schedule(self) -> dict:
        """Single payment this order implies, derived from ``payment_terms`` (op-bwo9).

        Returns ``{"due_date": date | None, "amount": Decimal, "basis": str}``.
        A **pure** derivation over fields this order already carries — no stored
        column, no ledger posting — so the web create-form can mirror the same
        math client-side before the order exists and every reader (detail page,
        ScanTTY) gets the identical answer from the API.

        ``due_date`` is null where the rule has nothing to anchor to: no terms
        agreed yet, or delivery-anchored terms on an order with no expected
        delivery date. ``amount`` is always the order's live estimated total, so
        voiding a line moves the payment down with it.
        """
        terms = self.payment_terms
        amount = self.effective_estimated_total
        net_days = self.NET_PAYMENT_DAYS.get(terms)

        if net_days is not None:
            due_date = self.order_date.date() + timedelta(days=net_days)
            basis = f"{self.PaymentTerms(terms).label} from order date"
        elif terms == self.PaymentTerms.PREPAID:
            due_date = self.order_date.date()
            basis = "Prepaid"
        elif terms in (self.PaymentTerms.DUE_ON_RECEIPT, self.PaymentTerms.COD):
            due_date = self.expected_delivery_date
            basis = "On delivery"
        else:
            due_date = None
            basis = "No payment terms set"

        return {"due_date": due_date, "amount": amount, "basis": basis}

    @property
    def has_active_items(self) -> bool:
        """Whether any line item on this PO is not voided."""
        return self._line_item_totals["total_items"] > 0

    @property
    def total_received_quantity(self) -> int:
        """Total quantity of all items received."""
        return self._line_item_totals["total_received_quantity"]

    @property
    def has_received_anything(self) -> bool:
        """Whether any quantity at all has physically arrived against this order.

        The one test of "did receiving ever happen here?", derived from the
        quantities the lines already carry rather than from a stored flag.
        Counts every line, voided ones included: goods that arrived and were
        then struck off still arrived.

        This is what keeps ``received`` meaning what it says. Settlement alone
        does not earn that status — a line can be settled by being written off
        or struck off, neither of which is a delivery — so an order nothing came
        in against stays where it is rather than reading "Fully Received" over a
        received quantity of zero.
        """
        return self.total_received_quantity > 0

    @property
    def is_fully_received(self) -> bool:
        """Whether every active line got at least the quantity that was ordered.

        False for an order with no active lines at all: an order whose every
        line was struck off, or that never carried one, cannot honestly claim
        the goods turned up. :attr:`is_settled` stays vacuously true for such an
        order — "receiving is finished with every active line" IS true of a set
        with no members — and that is the one the status derivation consumes.

        The strict reading, and NOT what decides the order's status — a line
        closed short leaves this False for ever, which is the honest answer to
        "did everything we ordered turn up?". :attr:`is_settled` is the
        question "is receiving finished with this order?".
        """
        return self._line_item_totals["is_fully_received"]

    @property
    def is_settled(self) -> bool:
        """Whether receiving is finished with every active line on this order.

        True once each line has either been received in full, over-received, or
        had its outstanding balance closed short. This — not
        :attr:`is_fully_received` — is what lets the order reach ``received``,
        so an order short-shipped by a vendor can be closed out and still carry
        the record of the shortfall.

        Settlement is necessary but not sufficient: the order also has to have
        taken something in (:attr:`has_received_anything`), because
        ``received`` is a claim that goods arrived. An order every line of which
        was written off or struck off without a delivery is settled and stays
        where it is.
        """
        return self._line_item_totals["is_settled"]

    @property
    def outstanding_line_count(self) -> int:
        """How many active lines receiving is still waiting on."""
        return self._line_item_totals["outstanding_line_count"]

    @property
    def variance_line_count(self) -> int:
        """How many settled lines did not match what was ordered (short or over)."""
        return self._line_item_totals["variance_line_count"]

    @property
    def has_receipt_variance(self) -> bool:
        """Whether any line on this order arrived short or over.

        The order-level flag the captain chases a vendor with: it stays true
        after the order is closed, because the point of recording a mismatch is
        that it is still visible later.
        """
        return self.variance_line_count > 0

    @property
    def days_since_ordered(self) -> int:
        """Days since the order was sent to supplier."""
        if self.sent_at:
            return (timezone.now() - self.sent_at).days
        return 0

    def calculate_estimated_total(self) -> Decimal:
        """Calculate estimated total cost from all line items."""
        total = sum((item.estimated_cost for item in self.items.all()), start=Decimal("0.00"))
        self.estimated_total = total
        return total

    def auto_generate_po_number(self) -> str:
        """Auto-generate a PO number if not set.

        Delegates the ``PO-YYYY-NNNN`` composition to
        :func:`reorder_queue.services.numbering.next_po_number` (gh #887) but
        stays an instance method: the concurrency test patches it, and
        ``save()`` calls it once per retry attempt on uniqueness collisions.
        """
        if not self.po_number:
            from .services.numbering import next_po_number

            self.po_number = next_po_number(timezone.now().year)
        return self.po_number

    def save(self, *args, **kwargs) -> None:
        """Ensure a PO number exists before saving.

        Concurrent creates can read the same "last" po_number and race to insert
        duplicates; retry with a fresh number on uniqueness collisions.
        """
        if self.po_number and self.pk:
            super().save(*args, **kwargs)
            return

        auto_assigned = not self.po_number
        max_retries = 5
        for attempt in range(max_retries):
            if auto_assigned:
                self.po_number = None
                self.auto_generate_po_number()
            try:
                with transaction.atomic():
                    super().save(*args, **kwargs)
                return
            except IntegrityError:
                if not auto_assigned or attempt == max_retries - 1:
                    raise


# Ordered typed-target slots for a PO line (#884). Unlike ChecklistStep this is
# an *at-most-one FK + freeform fallback* variant (item_supplier XOR asset, or a
# freeform description) — NOT strict exactly-one — so it reuses the accessor only
# and keeps its own hand-written CheckConstraint below. The inventory_item slot's
# target object lives one hop away, at item_supplier.item. Order matches the
# legacy get_item_type priority: item_supplier, then asset, then description.
_PO_ITEM_TARGETS = (
    TargetField("inventory_item", "item_supplier", value="item_supplier.item"),
    TargetField("asset", "asset"),
    TargetField("freeform", "description", has_object=False),
)


class PurchaseOrderItemQuerySet(models.QuerySet):
    """The database's half of the settlement derivation.

    :attr:`PurchaseOrderItem.is_settled` and friends answer for a line already
    in memory. A query cannot call a Python property, so every ORM site used to
    write its own predicate instead — which is how a metric came to count units
    somebody had explicitly written off as never arriving among the units still
    on their way, and how the admin changelist filed a closed-short line under
    "Partially Received" while the column beside it said "Closed short".

    This is that same derivation expressed in SQL, and it is not allowed to
    drift from the Python one by hand: ``test_settlement_sites`` builds a line
    for every combination of the settlement fields and asserts the two agree,
    row for row. Change one without the other and that test says so.
    """

    #: Alias :meth:`with_receipt_state` annotates under. Named rather than
    #: guessed at each call site so a filter and its annotation cannot disagree.
    RECEIPT_STATE_ALIAS = "derived_receipt_state"

    def with_receipt_state(self):
        """Annotate each row with the SQL twin of :attr:`PurchaseOrderItem.receipt_state`.

        Branch for branch in the same order as the property, because the order
        is load-bearing: an over-received line is also ``received >= ordered``,
        and a closed-short line that later filled up is received, not short.
        """
        states = self.model.ReceiptState
        return self.annotate(
            **{
                self.RECEIPT_STATE_ALIAS: Case(
                    When(is_voided=True, then=Value(states.VOIDED)),
                    When(
                        quantity_received__gt=F("quantity_ordered"),
                        then=Value(states.OVER_RECEIVED),
                    ),
                    When(
                        quantity_received__gte=F("quantity_ordered"),
                        then=Value(states.RECEIVED),
                    ),
                    When(self.model.q_closed_short(), then=Value(states.CLOSED_SHORT)),
                    When(quantity_received__gt=0, then=Value(states.PARTIALLY_RECEIVED)),
                    default=Value(states.NOT_RECEIVED),
                    output_field=models.CharField(),
                )
            }
        )

    def delete(self, *args, **kwargs):
        """Delete these lines, asking each affected order its status once.

        ``delete()`` fans ``post_delete`` out per row, and each of those is a
        settlement transition, so removing twenty lines would otherwise re-derive
        their order twenty times. Coalescing only — Django's own ``delete()``
        already owns the transaction.
        """
        from .settlement_signals import settlement_batch

        with settlement_batch():
            return super().delete(*args, **kwargs)

    # Django withholds ``delete`` from managers on purpose, and it does it with
    # an attribute rather than a name list: ``queryset_only`` is what stops
    # ``BaseManager._get_queryset_methods`` copying the method onto the manager.
    # It does NOT survive an override — redeclaring ``delete`` here without it
    # un-withholds it, and ``PurchaseOrderItem.objects.delete()`` becomes a bound,
    # callable method that takes no filter and empties the table.
    #
    # ``alters_data`` (which stops a template resolving ``{{ qs.delete }}`` into
    # a call) is a different story and is set here for symmetry, not to close a
    # hole: ``QuerySet`` inherits ``AltersData``, whose ``__init_subclass__``
    # copies ``alters_data`` onto a subclass's overrides automatically. Deleting
    # this line changes nothing on Django 6. Deleting the one above changes
    # everything, which is why the test asserts REACHABILITY on the manager
    # rather than the presence of either attribute.
    delete.queryset_only = True
    delete.alters_data = True

    def settled(self):
        """Lines receiving is finished with — the queryset twin of ``is_settled``."""
        return self.filter(self.model.q_settled())

    def outstanding(self):
        """Lines receiving is still waiting on — the queryset twin of ``outstanding_of``.

        Deliberately a ``Q`` rather than a filter on :meth:`with_receipt_state`:
        an annotation carried into ``.values(...).annotate(...)`` joins the
        GROUP BY and silently splits the very per-item totals the inventory
        metrics are grouping, so the aggregate sites need a predicate that adds
        no column.
        """
        return self.exclude(self.model.q_settled())


class PurchaseOrderItem(TypedTargetModel):
    """
    Line item within a purchase order.

    Represents a specific item or asset ordered from a supplier.
    Tracks received quantities and costs.

    Can be one of:
    - An inventory item (via item_supplier)
    - An asset (via asset)
    - A freeform item (via description, when neither item_supplier nor asset is set)
    """

    class ReceiptState(models.TextChoices):
        """What receiving still owes this line, and how the record differs from the order.

        DERIVED, never stored (:attr:`receipt_state`). A stored copy would be a
        second source of truth for something ``quantity_ordered``,
        ``quantity_received`` and ``closed_short_at`` already answer between
        them, and the two would drift the first time a quantity was edited.

        The three "settled" states — RECEIVED, OVER_RECEIVED, CLOSED_SHORT —
        plus VOIDED are what :attr:`is_settled` is the ``in`` test against, so
        adding a state here cannot leave a hand-maintained list behind.

        **Readers of this live outside ``reorder_queue``.** Deriving the
        consumers of a line's receipt state means searching the whole codebase,
        not this app: ``inventory.services.work_order_context`` builds the work
        order page's "ordered for this job" panel from these, and the Django
        admin renders them too. Six separate defects in this design's history
        came from a rule that reached all-but-one site, and the ones that got
        furthest were across an app boundary — the sweep that missed them had
        derived its consumers from ``reorder_queue`` alone.

        You do not have to do that search by hand any more, and should not:
        :mod:`reorder_queue.settlement_sites` derives the whole set from
        :attr:`is_settled` — this closure — and fails the build when a site
        decides settlement for itself. Run it, or read
        ``reorder_queue/tests/test_settlement_sites.py``.

        The distinction those readers keep getting wrong is worth stating once:
        :attr:`is_fully_received` answers "did the ordered quantity arrive?" and
        :attr:`is_settled` answers "is receiving finished with this line?". A
        line closed short answers no to the first and yes to the second, so a
        screen that asks the first while meaning the second shows a balance as
        still on its way for ever.
        """

        NOT_RECEIVED = "not_received", "Not received"
        PARTIALLY_RECEIVED = "partially_received", "Partially received"
        RECEIVED = "received", "Received in full"
        OVER_RECEIVED = "over_received", "Over-received"
        CLOSED_SHORT = "closed_short", "Closed short"
        VOIDED = "voided", "Voided"

    #: The states in which receiving is finished with a line — it is no longer
    #: outstanding and no longer blocks the order reaching RECEIVED.
    SETTLED_RECEIPT_STATES = frozenset(
        {
            ReceiptState.RECEIVED,
            ReceiptState.OVER_RECEIVED,
            ReceiptState.CLOSED_SHORT,
            ReceiptState.VOIDED,
        }
    )

    #: The settled states that do NOT match what was ordered — a variance the
    #: operator can chase the vendor with.
    VARIANCE_RECEIPT_STATES = frozenset({ReceiptState.OVER_RECEIVED, ReceiptState.CLOSED_SHORT})

    TARGET_FIELDS = _PO_ITEM_TARGETS
    # No TARGET_MODE: this is at-most-one + freeform, enforced by the existing
    # ``purchase_order_item_must_have_item_or_asset`` CheckConstraint, not the
    # mixin's exactly-one clean().

    #: Carries :class:`PurchaseOrderItemQuerySet` onto ``objects`` AND onto
    #: ``purchase_order.items``, so an order's own lines can be asked the
    #: settlement question the same way as the table can.
    objects = PurchaseOrderItemQuerySet.as_manager()

    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name="items"
    )
    item_supplier = models.ForeignKey(
        ItemSupplier,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Specific supplier relationship for inventory items",
    )
    asset = models.ForeignKey(
        "inventory.Asset",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="purchase_order_items",
        help_text="Asset being purchased (for asset purchases)",
    )
    description = models.CharField(
        max_length=500,
        blank=True,
        help_text="Freeform description for line items not linked to inventory items or assets",
    )
    # "Ordered to complete this job" (op-bu80). Orthogonal to the three-way
    # target above — an inventory, asset *or* freeform line can be bought for a
    # work order — so it is deliberately NOT a TARGET_FIELDS entry. Receiving a
    # line that carries this posts the received quantity back onto the work
    # order as an actual-cost material; see
    # ``inventory.services.work_order_purchase_bridge``.
    work_order = models.ForeignKey(
        "inventory.WorkOrder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_order_items",
        help_text=(
            "Work order this line was ordered for. Receiving it records the "
            "received quantity and its cost as a material on that work order."
        ),
    )
    # Per-line committee attribution (op-shb9). Same shape and intent as the
    # order-level field above — a single order can be split across committees,
    # so the line carries its own. Attribution only: the receiving ledger still
    # books the committee from the received item's own ``owning_group``.
    owning_group = models.ForeignKey(
        Group,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reorder_purchaseorderitem_owned",
        help_text="Committee (SIG) this line was ordered on behalf of",
    )
    # What this kit contained WHEN IT WAS ORDERED (op-8n0). A kit's bill of
    # materials is editable, and the gap between ordering and receiving is days
    # or weeks — so reading the live BOM at receipt time would credit whatever
    # the kit happens to contain today, not what the supplier actually shipped
    # in the box. Captured at line creation for kit lines and never rewritten.
    #
    # NULL for every ordinary item, asset and freeform line, which is what keeps
    # their payload byte-identical to the pre-kit shape, and also for kit lines
    # predating this field — those fall back to the live BOM, the only remaining
    # reason ``inventory.services.kits`` still reads it.
    #
    # Shape: ``{"components": [{"component": <pk>, "component_name": str,
    # "component_sku": str, "quantity_per_kit": int}, ...]}``. Quantities are
    # PER KIT, never multiplied out, so one snapshot serves the ordered-quantity
    # preview and each partial receipt alike.
    kit_snapshot = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "Component breakdown captured when this kit line was ordered. "
            "Receiving credits these components, not the kit's current "
            "definition. NULL for non-kit lines."
        ),
    )

    # Order quantities
    quantity_ordered = models.PositiveIntegerField(
        validators=[MinValueValidator(1)], help_text="Quantity ordered from supplier"
    )
    quantity_received = models.PositiveIntegerField(
        default=0, help_text="Quantity actually received"
    )
    order_in_packages = models.PositiveIntegerField(
        default=0,
        help_text="Number of packages ordered (calculated from quantity_ordered / quantity_per_package for inventory items)",
    )

    # Pricing
    unit_cost_ordered = models.DecimalField(
        max_digits=10, decimal_places=4, help_text="Unit cost at time of ordering"
    )
    unit_cost_actual = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Actual unit cost charged",
    )

    # Shipment tracking
    expected_shipment_date = models.DateField(
        null=True,
        blank=True,
        help_text="Expected shipment date for this line item (useful for items with longer lead times)",
    )
    actual_shipment_date = models.DateField(
        null=True,
        blank=True,
        help_text=(
            "Actual date the supplier reported this line item shipped. "
            "Separate from delivery — scantty / the web UI flip this when a "
            "supplier confirms shipment so the operator can see the gap between "
            "expected_shipment_date and reality."
        ),
    )

    # Status
    is_voided = models.BooleanField(
        default=False,
        help_text="Whether this line item has been voided (e.g., item discontinued by supplier)",
    )
    voided_at = models.DateTimeField(
        null=True, blank=True, help_text="When this line item was voided"
    )
    voided_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="voided_purchase_order_items",
        help_text="User who voided this line item",
    )
    void_reason = models.TextField(blank=True, help_text="Reason for voiding this line item")

    # Closed short: the operator has declared that the outstanding balance on
    # this line is never arriving, so receiving is finished with it even though
    # less than the ordered quantity came in. Deliberately NOT a boolean beside
    # a timestamp — ``closed_short_at`` alone answers "is it closed short?"
    # (:attr:`is_closed_short`), so there is no pair of fields to disagree.
    closed_short_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When the outstanding balance on this line was written off as never "
            "arriving. Null means the line is still expecting the rest."
        ),
    )
    closed_short_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="closed_short_purchase_order_items",
        help_text="User who declared the outstanding balance would not arrive",
    )
    closed_short_reason = models.TextField(
        blank=True,
        help_text="Why the outstanding balance was written off (backorder cancelled, vendor short-shipped, ...)",
    )

    # Reopened: a close-short taken back. A CORRECTION, never an undo — the
    # ``closed_short_*`` stamps above are left exactly as they were, so the line
    # reads as a mistake and its correction rather than as a clean slate. Which
    # of the two is in force is decided by comparing the timestamps
    # (:attr:`is_closed_short`), so there is no boolean here to disagree with
    # them and every reader keeps the one derivation.
    reopened_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When a close-short on this line was taken back. Null means the line "
            "has never been reopened."
        ),
    )
    reopened_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reopened_purchase_order_items",
        help_text="User who took the close-short back",
    )
    reopened_reason = models.TextField(
        blank=True,
        help_text="Why the close-short was taken back (closed the wrong line, the balance shipped after all, ...)",
    )

    # Notes
    notes = models.TextField(blank=True)

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["purchase_order", "item_supplier__item__name", "asset__name"]
        unique_together = [
            ["purchase_order", "item_supplier"],
            ["purchase_order", "asset"],
        ]
        indexes = [
            models.Index(fields=["purchase_order", "item_supplier"]),
            models.Index(fields=["purchase_order", "asset"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(item_supplier__isnull=False, asset__isnull=True)
                    | models.Q(item_supplier__isnull=True, asset__isnull=False)
                    | (
                        models.Q(item_supplier__isnull=True, asset__isnull=True)
                        & ~models.Q(description="")
                    )
                ),
                name="purchase_order_item_must_have_item_or_asset",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.target_label} - {self.quantity_ordered} units"

    @property
    def target_label(self) -> str:
        """Human label for this line, never dereferencing a null target.

        Routes the three-way label through the typed-target accessor (#884):
        inventory_item -> item.name, asset -> asset.name, freeform -> generic.
        Single home for "what is this line called?", shared with
        ``DeliveryItem.__str__`` and the admin item columns so none of them
        repeat the ``.item.name`` dereference that null-crashed on asset-only
        lines (BACKEND-13).
        """
        target = self.target
        return target.name if target is not None else "Purchase Order Item"

    @property
    def item(self) -> Optional[InventoryItem]:
        """Convenience property to access the inventory item (if applicable)."""
        return self.target if self.target_type == "inventory_item" else None

    @property
    def is_kit_line(self) -> bool:
        """Whether this line buys a kit SKU that decomposes on receipt (op-8n0).

        Derived, never stored: a kit is an ``InventoryItem`` carrying
        ``is_kit``, so the line already points at it through ``item_supplier``
        and needs no column of its own. Receiving one credits the kit's
        components rather than the kit — see
        ``inventory.services.kits.explode_kit_receipt``.
        """
        item = self.item
        return item is not None and item.is_kit

    @property
    def supplier(self) -> Optional[Supplier]:
        """Convenience property to access the supplier."""
        if self.target_type == "inventory_item":
            return self.item_supplier.supplier
        if self.target_type == "asset":
            return self.asset.manufacturer
        return None

    @property
    def estimated_cost(self) -> Decimal:
        """Calculate estimated total cost for this line item."""
        if self.quantity_ordered is None or self.unit_cost_ordered is None:
            return Decimal("0.00")
        return self.quantity_ordered * self.unit_cost_ordered

    @property
    def actual_cost(self) -> Optional[Decimal]:
        """Calculate actual total cost if unit cost is known."""
        if self.unit_cost_actual is not None and self.quantity_received > 0:
            return self.quantity_received * self.unit_cost_actual
        return None

    @property
    def is_fully_received(self) -> bool:
        """Check if the full ordered quantity has been received."""
        if self.quantity_ordered is None:
            return False
        return self.quantity_received >= self.quantity_ordered

    @property
    def quantity_pending(self) -> int:
        """Calculate quantity still pending delivery.

        Floored at zero: an over-received line has nothing left to expect, and
        a negative "pending" would read as an order for goods. The signed
        difference an over-receipt creates lives in :attr:`quantity_variance`,
        which is what the flag on the order is rendered from.
        """
        if self.quantity_ordered is None:
            return 0
        return max(0, self.quantity_ordered - self.quantity_received)

    @property
    def quantity_variance(self) -> int:
        """Signed difference between what arrived and what was ordered.

        Negative = short, positive = over, zero = exactly as ordered. The
        honest figure: unlike :attr:`quantity_pending` it is never floored, so
        an over-receipt stays visible as the ``+2`` it actually was rather than
        being rounded away to "nothing pending".
        """
        if self.quantity_ordered is None:
            return 0
        return self.quantity_received - self.quantity_ordered

    @property
    def is_closed_short(self) -> bool:
        """Whether the outstanding balance is currently written off as never arriving.

        Read off the two stamps and nothing else, which is what lets a reopen be
        a correction rather than an erasure: reopening leaves
        :attr:`closed_short_at` and its reason and actor in place and stamps
        :attr:`reopened_at` beside them, and the later of the two is the one in
        force. :attr:`receipt_state` and :attr:`is_settled` are both built on
        this, so no reader has to know a reopened line is a special case.
        """
        if self.closed_short_at is None:
            return False
        if self.reopened_at is None:
            return True
        return self.closed_short_at > self.reopened_at

    @property
    def was_reopened(self) -> bool:
        """Whether a close-short on this line was taken back and is not back in force."""
        return self.reopened_at is not None and not self.is_closed_short

    @property
    def is_over_received(self) -> bool:
        """Whether more arrived than was ordered."""
        return self.quantity_variance > 0

    @property
    def is_short_received(self) -> bool:
        """Whether the line was closed with less than the ordered quantity in hand.

        A line that is merely partially received is NOT short: the rest is
        still expected. It becomes short only once somebody says it is not
        coming.
        """
        return self.is_closed_short and self.quantity_variance < 0

    @property
    def receipt_state(self) -> str:
        """This line's position in the receiving workflow — see :class:`ReceiptState`.

        The single derivation every reader shares (API, admin, the order's own
        roll-up), so a line described as "closed short" on one screen cannot be
        "partially received" on another. Code holding a QUERYSET rather than a
        line asks the same question through
        :meth:`PurchaseOrderItemQuerySet.with_receipt_state`, which is this
        branch for branch and is held to it by test.
        """
        if self.is_voided:
            return self.ReceiptState.VOIDED
        if self.is_over_received:
            return self.ReceiptState.OVER_RECEIVED
        if self.quantity_ordered is not None and self.quantity_received >= self.quantity_ordered:
            return self.ReceiptState.RECEIVED
        if self.is_closed_short:
            return self.ReceiptState.CLOSED_SHORT
        if self.quantity_received > 0:
            return self.ReceiptState.PARTIALLY_RECEIVED
        return self.ReceiptState.NOT_RECEIVED

    @property
    def receipt_state_label(self) -> str:
        """Human label for :attr:`receipt_state`, from the choices themselves."""
        return self.ReceiptState(self.receipt_state).label

    @property
    def is_settled(self) -> bool:
        """Whether receiving is finished with this line.

        Settled covers four different endings — received in full, over-received,
        closed short, and voided — and is what decides whether the line still
        blocks the order reaching ``received``. It is emphatically NOT the same
        question as :attr:`is_fully_received`: a line closed two units short is
        settled and not fully received, and both facts stay on the record.
        """
        return self.receipt_state in self.SETTLED_RECEIPT_STATES

    @property
    def has_receipt_variance(self) -> bool:
        """Whether this line's settled record differs from what was ordered."""
        return self.receipt_state in self.VARIANCE_RECEIPT_STATES

    # -- the same three answers, for code that has a query rather than a line --
    #
    # A queryset cannot call a property, so every ORM site used to write out its
    # own version of these and each one got a different subset of the fields
    # right. They live here, beside the properties they mirror, so the two are
    # read and changed together; ``test_settlement_sites`` asserts they agree for
    # every combination of the settlement fields rather than trusting that.

    @classmethod
    def q_closed_short(cls) -> Q:
        """SQL twin of :attr:`is_closed_short`.

        The later of the two stamps wins, and a line never reopened is still
        closed — the ``reopened_at IS NULL`` arm is not optional, because SQL
        comparisons against NULL are NULL rather than true.
        """
        return Q(closed_short_at__isnull=False) & (
            Q(reopened_at__isnull=True) | Q(closed_short_at__gt=F("reopened_at"))
        )

    @classmethod
    def q_settled(cls) -> Q:
        """SQL twin of :attr:`is_settled` — receiving is finished with the line.

        The same four endings, in one predicate: struck off, everything (or
        more) arrived, or the balance was written off and not taken back.
        """
        return (
            Q(is_voided=True)
            | Q(quantity_received__gte=F("quantity_ordered"))
            | cls.q_closed_short()
        )

    @classmethod
    def outstanding_quantity_expression(cls):
        """SQL twin of :attr:`quantity_pending`, floored at zero the same way.

        Meaningful only about a line that is not settled — a closed-short line
        keeps a non-zero value here, which is exactly the trap: pair it with
        :meth:`q_settled` (or ``PurchaseOrderItem.objects.outstanding()``)
        rather than summing it over everything.
        """
        return Greatest(
            F("quantity_ordered") - F("quantity_received"),
            Value(0),
            output_field=models.IntegerField(),
        )

    def close_short(self, *, actor=None, reason: str = "", at=None) -> None:
        """Write off this line's outstanding balance as never arriving.

        Idempotent-by-refusal rather than idempotent: re-closing an already
        closed line, or closing one that has nothing outstanding, raises so a
        caller cannot quietly overwrite the recorded reason and actor of the
        first close. ``ValidationError`` so DRF renders it as a 400.

        A line that was closed short and then reopened may be closed again —
        the reopen put it back in receiving, so it can end short a second time.
        Both stamps are re-read by :attr:`is_closed_short`, so the later one is
        the one in force with no third field to keep in step.
        """
        if self.is_voided:
            raise ValidationError("A voided line has nothing outstanding to close short.")
        if self.is_closed_short:
            raise ValidationError("This line has already been closed short.")
        if self.quantity_pending == 0:
            raise ValidationError(
                "This line has nothing outstanding — it is already received in full."
            )
        self.closed_short_at = at or timezone.now()
        self.closed_short_by = actor if (actor is not None and actor.is_authenticated) else None
        self.closed_short_reason = reason
        self.save(
            update_fields=[
                "closed_short_at",
                "closed_short_by",
                "closed_short_reason",
                "updated_at",
            ]
        )

    def reopen_short(self, *, actor=None, reason: str = "", at=None) -> None:
        """Take back a close-short: put this line's outstanding balance back on the order.

        A CORRECTION, not an undo. The close-short is left on the line
        untouched — ``closed_short_at``, ``closed_short_by`` and
        ``closed_short_reason`` all keep their values — and the reopen is
        stamped beside it to the same standard, actor and timestamp and reason,
        so the record reads as a mistake and its correction rather than as
        something that never happened.

        Refuses a line that is not currently closed short, rather than stamping
        a correction over nothing. ``ValidationError`` so DRF renders it as a
        400.
        """
        if not self.is_closed_short:
            raise ValidationError("This line is not closed short, so there is nothing to reopen.")
        self.reopened_at = at or timezone.now()
        self.reopened_by = actor if (actor is not None and actor.is_authenticated) else None
        self.reopened_reason = reason
        self.save(
            update_fields=[
                "reopened_at",
                "reopened_by",
                "reopened_reason",
                "updated_at",
            ]
        )


class PurchaseOrderAttachment(models.Model):
    """
    A file attached to a purchase order after creation.

    Used to attach supporting documents (sales orders, supplier confirmations,
    receipts, etc.) once the PO is in flight.
    """

    purchase_order = models.ForeignKey(
        PurchaseOrder,
        on_delete=models.CASCADE,
        related_name="attachments",
        help_text="The purchase order this attachment belongs to",
    )
    file = models.FileField(
        upload_to="purchase_orders/attachments/%Y/%m/",
        help_text="Attached document",
    )
    description = models.CharField(max_length=500, blank=True)
    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_order_attachments",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at"]

    def __str__(self) -> str:
        return f"Attachment for PO #{self.purchase_order.po_number} ({self.uploaded_at.date()})"


class OrderDelivery(models.Model):
    """
    Tracks a delivery/receipt event for a purchase order.

    A purchase order may have multiple deliveries (partial shipments).
    Each delivery records what was actually received and when.
    """

    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name="deliveries"
    )

    # Delivery details
    delivery_date = models.DateTimeField(default=timezone.now)
    tracking_number = models.CharField(max_length=100, blank=True)
    carrier = models.CharField(max_length=100, blank=True)

    # Receipt details
    received_by = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="received_deliveries"
    )
    receipt_notes = models.TextField(blank=True)

    # Status
    is_complete = models.BooleanField(
        default=False,
        help_text="Mark as complete when all items in this delivery are processed",
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-delivery_date"]
        indexes = [
            models.Index(fields=["purchase_order", "-delivery_date"]),
            models.Index(fields=["received_by", "-delivery_date"]),
        ]

    def __str__(self) -> str:
        return f"Delivery for PO #{self.purchase_order.po_number} on {self.delivery_date.date()}"

    @property
    def total_items_received(self) -> int:
        """Total number of distinct items received in this delivery."""
        return self.items.count()

    @property
    def total_quantity_received(self) -> int:
        """Total quantity of all items received in this delivery."""
        return sum(item.quantity_received for item in self.items.all())


class DeliveryItem(models.Model):
    """
    Individual item received in a delivery.

    Records the actual quantity and condition of each item received.
    Used for barcode scanning and inventory updates.
    """

    delivery = models.ForeignKey(OrderDelivery, on_delete=models.CASCADE, related_name="items")
    purchase_order_item = models.ForeignKey(
        PurchaseOrderItem, on_delete=models.CASCADE, related_name="deliveries"
    )

    # Receipt details
    quantity_received = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        help_text="Quantity actually received for this item",
    )

    # Quality/condition tracking
    is_damaged = models.BooleanField(default=False)
    is_expired = models.BooleanField(default=False)
    condition_notes = models.TextField(blank=True)

    # Barcode/scanning info
    scanned_upc = models.CharField(
        max_length=50, blank=True, help_text="UPC code scanned during receipt"
    )
    scanned_at = models.DateTimeField(null=True, blank=True)
    scanned_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scanned_items",
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["delivery", "purchase_order_item"]
        indexes = [
            models.Index(fields=["delivery", "purchase_order_item"]),
            models.Index(fields=["scanned_upc", "scanned_at"]),
        ]

    def __str__(self) -> str:
        # ``purchase_order_item.item`` is None on asset-only and freeform lines,
        # so label through the line's typed-target accessor instead of
        # dereferencing ``.item.name`` (BACKEND-13: the admin delete-confirmation
        # page str()s every cascade-related object, and a received asset line
        # under a supplier being deleted took the whole page down with it).
        return f"{self.purchase_order_item.target_label} - {self.quantity_received} received"

    @property
    def item(self) -> Optional[InventoryItem]:
        """The inventory item, or None for asset-only / freeform lines."""
        return self.purchase_order_item.item

    @property
    def supplier(self) -> Optional[Supplier]:
        """The supplier, or None when the line carries no resolvable supplier."""
        return self.purchase_order_item.supplier


class LeadTimeLog(models.Model):
    """
    Historical lead time tracking for supplier performance analysis.

    Records actual delivery performance vs. estimated lead times
    to improve future ordering decisions and supplier evaluation.
    """

    item_supplier = models.ForeignKey(
        ItemSupplier, on_delete=models.CASCADE, related_name="lead_time_logs"
    )
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name="lead_time_logs"
    )

    # Time tracking
    order_date = models.DateTimeField(help_text="When the order was placed")
    expected_delivery_date = models.DateField(help_text="When delivery was expected")
    actual_delivery_date = models.DateField(help_text="When delivery actually occurred")

    # Lead time calculations (in business days)
    estimated_lead_time_days = models.PositiveIntegerField(
        help_text="Estimated lead time in business days"
    )
    actual_lead_time_days = models.PositiveIntegerField(
        help_text="Actual lead time in business days"
    )
    variance_days = models.IntegerField(
        help_text="Difference between actual and estimated (positive = late)"
    )

    # Order details
    quantity_ordered = models.PositiveIntegerField()
    quantity_received = models.PositiveIntegerField()

    # Metadata
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-actual_delivery_date"]
        indexes = [
            models.Index(fields=["item_supplier", "-actual_delivery_date"]),
            models.Index(fields=["purchase_order"]),
            models.Index(fields=["actual_delivery_date"]),
        ]

    def __str__(self) -> str:
        item_name = self.item_supplier.item.name
        supplier_name = self.item_supplier.supplier.name
        return f"{item_name} from {supplier_name} - {self.actual_lead_time_days} days"

    @property
    def item(self) -> InventoryItem:
        """Convenience property to access the inventory item."""
        return self.item_supplier.item

    @property
    def supplier(self) -> Supplier:
        """Convenience property to access the supplier."""
        return self.item_supplier.supplier

    @property
    def was_late(self) -> bool:
        """Check if the delivery was late."""
        return self.variance_days > 0

    @property
    def was_early(self) -> bool:
        """Check if the delivery was early."""
        return self.variance_days < 0

    @classmethod
    def calculate_business_days(cls, start_date, end_date) -> int:
        """Calculate business days between two dates (excluding weekends)."""
        from datetime import timedelta

        if isinstance(start_date, timezone.datetime):
            start_date = start_date.date()
        if isinstance(end_date, timezone.datetime):
            end_date = end_date.date()

        if start_date > end_date:
            return 0

        business_days = 0
        current_date = start_date

        while current_date <= end_date:
            # Monday = 0, Sunday = 6
            if current_date.weekday() < 5:  # Monday to Friday
                business_days += 1
            current_date += timedelta(days=1)

        return business_days

    def save(self, *args, **kwargs):
        """Auto-calculate variance when saving."""
        self.variance_days = self.actual_lead_time_days - self.estimated_lead_time_days
        super().save(*args, **kwargs)


class WebHook(models.Model):
    """
    Generic webhook configuration for event notifications.

    Webhooks allow external systems to be notified when specific events occur,
    such as reorder requests, low stock alerts, delivery notifications, etc.
    """

    class EventType(models.TextChoices):
        REORDER_REQUEST_CREATED = "reorder_request_created", "Reorder Request Created"
        REORDER_REQUEST_APPROVED = "reorder_request_approved", "Reorder Request Approved"
        REORDER_REQUEST_ORDERED = "reorder_request_ordered", "Reorder Request Ordered"
        REORDER_REQUEST_RECEIVED = "reorder_request_received", "Reorder Request Received"
        ITEM_LOW_STOCK = "item_low_stock", "Item Low Stock"
        PURCHASE_ORDER_CREATED = "purchase_order_created", "Purchase Order Created"
        DELIVERY_RECEIVED = "delivery_received", "Delivery Received"
        FIXTURE_REFILL_REQUESTED = "fixture_refill_requested", "Fixture Refill Requested"
        LOCATION_CHECKIN = "location_checkin", "Location Check-in"
        LOCATION_FEEDBACK = "location_feedback", "Location Feedback"
        SECURITY_REPORT = "security_report", "Security Report"
        LOCATION_PROBLEM_REPORTED = "location_problem_reported", "Location Problem Reported"

    # Core fields
    name = models.CharField(max_length=200, help_text="Descriptive name for this webhook")
    url = models.URLField(help_text="Webhook endpoint URL to POST notifications to")
    event_type = models.CharField(
        max_length=50,
        choices=EventType.choices,
        help_text="Type of event that triggers this webhook",
    )

    # Configuration
    is_active = models.BooleanField(
        default=True, help_text="Enable or disable this webhook without deleting it"
    )
    secret = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional secret key for HMAC signature verification",
    )
    headers = models.JSONField(
        default=dict,
        blank=True,
        help_text="Optional custom HTTP headers to send with webhook (as JSON object)",
    )

    # Metadata
    description = models.TextField(blank=True, help_text="Description of what this webhook does")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Statistics
    last_triggered_at = models.DateTimeField(
        null=True, blank=True, help_text="When this webhook was last triggered"
    )
    success_count = models.PositiveIntegerField(
        default=0, help_text="Number of successful webhook deliveries"
    )
    failure_count = models.PositiveIntegerField(
        default=0, help_text="Number of failed webhook deliveries"
    )
    last_error = models.TextField(
        blank=True, help_text="Last error message if webhook delivery failed"
    )

    class Meta:
        ordering = ["event_type", "name"]
        indexes = [
            models.Index(fields=["event_type", "is_active"]),
            models.Index(fields=["is_active", "-last_triggered_at"]),
        ]

    def __str__(self) -> str:
        status = "✓" if self.is_active else "✗"
        return f"{status} {self.name} ({self.get_event_type_display()})"

    @property
    def success_rate(self) -> float:
        """Calculate success rate percentage."""
        total = self.success_count + self.failure_count
        if total == 0:
            return 0.0
        return (self.success_count / total) * 100

    def record_success(self) -> None:
        """Record a successful webhook delivery."""
        self.success_count += 1
        self.last_triggered_at = timezone.now()
        self.last_error = ""
        self.save(update_fields=["success_count", "last_triggered_at", "last_error"])

    def record_failure(self, error_message: str) -> None:
        """Record a failed webhook delivery.

        ``error_message`` is scrubbed through ``observability_redaction.redact``
        before storage (gh #378). Webhook delivery errors frequently quote the
        outbound request's headers (Authorization / Cookie) or the upstream's
        response body — both of which can carry secrets even when the webhook
        config itself is benign. The redactor's value-shape pass strips
        ``Bearer …`` tokens, JWTs, PEM blocks, and high-entropy hex/base64
        sequences before the row is written, so reading ``last_error`` from
        the admin or shell never re-leaks the credential.
        """
        self.failure_count += 1
        self.last_triggered_at = timezone.now()
        # Truncate AFTER redaction so REDACTED placeholders don't get sliced.
        self.last_error = redact(error_message)[:1000]
        self.save(update_fields=["failure_count", "last_triggered_at", "last_error"])


class PurchaseOrderAuditEvent(models.Model):
    """Append-only audit log for safety-critical purchase-order mutations.

    Captures actor, timestamp, affected entity (PO / line item / attachment),
    notes, and metadata for each meaningful state change. Rows are written
    by ``reorder_queue.audit.record_event`` and never updated or deleted by
    application code.

    Per gh #353 / #334. Pattern mirrors
    ``forgekey.models.ForgeKeyAuditEvent`` (gh #352) so the eventual unified
    review surface (gh #359) can join across domains cleanly.
    """

    class Action(models.TextChoices):
        PO_CREATE = "po_create", "Purchase order created"
        PO_SEND = "po_send", "Purchase order sent to supplier"
        PO_VOID = "po_void", "Purchase order voided"
        PO_LINE_ADD = "po_line_add", "Purchase order line item added"
        PO_LINE_VOID = "po_line_void", "Purchase order line item voided"
        # The line itself is gone, so this row's ``line_item`` FK is SET_NULL
        # the moment it is written. That is the point of recording it: the
        # ``purchase_order`` FK and the metadata below are what is left of the
        # line, and they are written in full for exactly that reason.
        PO_LINE_DELETE = "po_line_delete", "Purchase order line item deleted"
        PO_LINE_REPRICE = "po_line_reprice", "Purchase order line item repriced"
        PO_MARK_DELIVERED = "po_mark_delivered", "Purchase order marked delivered"
        PO_RECEIVE_ITEMS = "po_receive_items", "Purchase order line items received"
        PO_LINE_REOPEN_SHORT = (
            "po_line_reopen_short",
            "Purchase order line item reopened after being closed short",
        )
        ATTACHMENT_ADD = "attachment_add", "Attachment added"
        ATTACHMENT_REMOVE = "attachment_remove", "Attachment removed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_order_audit_actions",
        help_text="User who performed the action; null for system-initiated events.",
    )
    action = models.CharField(max_length=32, choices=Action.choices)
    # Optional FKs to the entities involved. At least one is set per row;
    # SET_NULL on delete so the audit trail survives entity teardown.
    purchase_order = models.ForeignKey(
        "PurchaseOrder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    line_item = models.ForeignKey(
        "PurchaseOrderItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    attachment = models.ForeignKey(
        "PurchaseOrderAttachment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    notes = models.TextField(blank=True)
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Action-specific payload (delivery date, void reason, attachment filename, etc).",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["purchase_order", "-created_at"], name="po_audit_po_idx"),
            models.Index(fields=["actor", "-created_at"], name="po_audit_actor_idx"),
            models.Index(fields=["action", "-created_at"], name="po_audit_action_idx"),
        ]

    def __str__(self) -> str:
        target = self.purchase_order_id or self.line_item_id or self.attachment_id
        return f"{self.action} ({target}) @ {self.created_at:%Y-%m-%d %H:%M}"


class WebhookAuditEvent(models.Model):
    """Append-only audit log for webhook configuration + lifecycle events.

    Captures actor, action, webhook reference, notes, and per-action
    metadata (incl. delta for config-field changes). Rows are written by
    ``reorder_queue.webhook_audit.record_event`` and never updated or
    deleted by application code.

    Per gh #357 / #334. Mirrors the per-domain audit tables in #352-#356.

    Inbound auth failures and outbound delivery failures are intentionally
    NOT captured here — both are high-volume per-event and are summarized
    on the WebHook row itself (failure_count + last_error). A future
    aggregated-stat table can land separately if event-level capture
    becomes operationally necessary.
    """

    class Action(models.TextChoices):
        WEBHOOK_CREATE = "webhook_create", "Webhook created"
        WEBHOOK_UPDATE = "webhook_update", "Webhook config updated"
        WEBHOOK_DELETE = "webhook_delete", "Webhook deleted"
        WEBHOOK_DISABLE = "webhook_disable", "Webhook disabled"
        WEBHOOK_ENABLE = "webhook_enable", "Webhook enabled"
        WEBHOOK_SECRET_ROTATE = (
            "webhook_secret_rotate",  # nosec B105 — action name, not a credential
            "Webhook secret rotated",
        )

    # Non-secret config fields the audit hook tracks for diff capture.
    # ``secret`` is intentionally excluded — its value never appears in
    # audit metadata; only the fact that it changed is recorded via the
    # `webhook_secret_rotate` action.
    AUDITED_FIELDS = (
        "name",
        "url",
        "event_type",
        "is_active",
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="webhook_audit_actions",
        help_text="User who performed the action; null for system-initiated events.",
    )
    action = models.CharField(max_length=32, choices=Action.choices)
    webhook = models.ForeignKey(
        WebHook,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    notes = models.TextField(blank=True)
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Action-specific payload. For webhook_update, includes a "
            "'changes' dict mapping field name -> {before, after}. The "
            "webhook secret is NEVER included — only the fact that it "
            "changed is recorded."
        ),
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["webhook", "-created_at"], name="webhook_audit_wh_idx"),
            models.Index(fields=["actor", "-created_at"], name="webhook_audit_actor_idx"),
            models.Index(fields=["action", "-created_at"], name="webhook_audit_action_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.action} ({self.webhook_id}) @ {self.created_at:%Y-%m-%d %H:%M}"
