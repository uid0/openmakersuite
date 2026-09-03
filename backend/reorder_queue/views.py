"""
Views for reorder queue API.
"""

import csv
import io
import re
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models, transaction
from django.db.models import Avg, Count, F, Max, Min, Q, Sum
from django.utils import timezone

from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from inventory.models import InventoryItem, Supplier
from inventory.services.pack_size import declares_a_case
from inventory.services.packaging import (
    base_reorder_quantity,
    count_unit,
    counts_in_packs,
    low_stock_q,
    on_hand_display,
    parse_at_level,
    reorder_display,
    resolve_base_quantity,
)
from inventory.services.pricing import (
    PriceRollup,
    explain,
    package_price_of,
    unit_price_of,
)
from inventory.services.supplier_selection import (
    NO_SUPPLIERS,
    item_suppliers_prefetch,
    primary_item_supplier,
    select_supplier,
)

from . import services
from .audit import record_event as record_audit_event
from .audit import record_line_reprice
from .models import (
    DeliveryItem,
    LeadTimeLog,
    OrderDelivery,
    PurchaseOrder,
    PurchaseOrderAttachment,
    PurchaseOrderAuditEvent,
    PurchaseOrderItem,
    ReorderRequest,
    WebHook,
    WebhookAuditEvent,
)
from .serializers import (
    AddPurchaseOrderLineSerializer,
    BarcodeReceiptSerializer,
    CloseShortSerializer,
    MarkDeliveredSerializer,
    MarkReceivedSerializer,
    OrderDeliverySerializer,
    OrderMetricsSerializer,
    PurchaseOrderAttachmentSerializer,
    PurchaseOrderCreateSerializer,
    PurchaseOrderSerializer,
    ReceiveItemsSerializer,
    ReopenShortSerializer,
    ReorderRequestCreateSerializer,
    ReorderRequestSerializer,
    RepricePurchaseOrderLineSerializer,
    SupplierPerformanceSerializer,
    WebHookCreateSerializer,
    WebHookSerializer,
    WebHookTestResultSerializer,
)
from .webhook_audit import diff_audited_fields as diff_webhook_audited_fields
from .webhook_audit import record_event as record_webhook_audit_event


def _po_line_display_name(po_item):
    """Human-readable name for a purchase-order line.

    Used only to label ``missing_sku`` entries so an operator can tell which
    line needs a supplier part number fixed. Prefers the inventory item name,
    then the asset name, then the freeform description.
    """
    item = po_item.item  # item_supplier.item, or None for asset/freeform lines
    if item is not None:
        return item.name
    if po_item.asset is not None:
        return po_item.asset.name
    return po_item.description or f"Line {po_item.pk}"


# Per-supplier ordering adapters (op-svpq). A supplier's ``ordering_adapter``
# selects which artifact the order-pad export emits: a generic part#,qty pad, an
# Amazon add-to-cart URL, or an HD Supply Part#,Qty CSV. The SKU validators below
# are reused by the export so a mis-typed part number is surfaced in
# ``invalid_sku`` before it ever reaches a vendor cart, rather than silently
# ordering the wrong thing.

# An Amazon ASIN is exactly ten uppercase alphanumerics (e.g. ``B07X1234YZ``).
ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
# HD Supply part numbers are numeric.
HDSUPPLY_PART_RE = re.compile(r"^[0-9]+$")

# Amazon's public add-to-cart endpoint. Served by Amazon retail and independent
# of the Product Advertising API (which sunsets 2026-05-15) — RE-VERIFY it still
# resolves after that date. A plain GET: NO AssociateTag and NO request signing.
# The user must be signed in to amazon.com to complete the cart (the link
# redirects to sign-in preserving the params) and may hit a "Confirm Your Action"
# interstitial — it is the pragmatic path, not a guaranteed one-click, and
# Amazon Business does not officially honor it.
AMAZON_CART_BASE = "https://www.amazon.com/gp/aws/cart/add.html"
# Keep each cart URL comfortably under common ~2000-char URL limits; longer POs
# are chunked across multiple URLs.
AMAZON_URL_MAX_LEN = 2000


def _resolve_receive_serials(po_item, serial_payloads):
    """Turn a line's ``serials`` payload into :class:`services.SerialCapture` records.

    Resolves the ``item`` each serial names — and, where the payload omits it,
    supplies the only identity it could mean. That default is deliberately
    narrow: it applies ONLY when the line credits exactly one serialized
    identity. A kit line crediting several serialized components has no "only
    identity", so an unlabelled serial there is refused with the choices named
    rather than being attached to whichever component sorted first.

    Naming the kit itself is refused by
    :func:`~reorder_queue.services.receiving.resolve_serial_targets`, which sees
    the resolved captures; nothing here can smuggle one past it, because an id
    that is not among the receipt's serialized targets never resolves.

    Raises ``django.core.exceptions.ValidationError``; the caller renders it as
    a 400 naming the line.
    """
    if not serial_payloads:
        return []

    from inventory.models import InventoryItem

    # What the ORDERED quantity implies, which is the widest set a serial on
    # this line could legitimately name. The receipt then re-checks the
    # captures against THIS receipt's quantity, so a serial for a real
    # component still fails if more were sent than this delivery credits.
    targets = {
        item.pk: item
        for item, _ in services.serialized_receipt_targets(po_item, po_item.quantity_ordered or 0)
    }

    # Naming the kit is answered with the kit's own explanation FIRST, before
    # the generic "nothing here is serialized" below. A kit whose components
    # happen not to be serialized would otherwise send the operator looking for
    # a serial setting, when the actual mistake is that they aimed the serial
    # at a SKU that never enters stock.
    line_item = po_item.item
    if line_item is not None and line_item.is_kit:
        for payload in serial_payloads:
            if payload.get("item") == line_item.pk:
                raise DjangoValidationError(
                    f"the kit '{line_item.name}' is never itself stocked — record serials "
                    "against the components the receipt credits, not against the kit"
                )

    if not targets:
        raise DjangoValidationError(
            "nothing on this line is serialized, so it cannot carry serial numbers"
        )

    sole_target = next(iter(targets.values())) if len(targets) == 1 else None

    captures = []
    for payload in serial_payloads:
        item_id = payload.get("item")
        if item_id is None:
            if sole_target is None:
                names = ", ".join(sorted(item.name for item in targets.values()))
                raise DjangoValidationError(
                    "this line credits several serialized items, so each serial must say "
                    f"which one it belongs to (one of: {names})"
                )
            item = sole_target
        else:
            item = targets.get(item_id)
            if item is None:
                # Named something real but not something this line credits —
                # the kit's own id lands here too, and the service's error
                # spells out why that one in particular is wrong.
                item = InventoryItem.objects.filter(pk=item_id).first()
                if item is None:
                    raise DjangoValidationError(f"no inventory item with id {item_id}")
        captures.append(
            services.SerialCapture(
                item=item,
                serial_number=payload["serial_number"],
                lot=payload.get("lot", "") or "",
                expiration_date=payload.get("expiration_date"),
            )
        )
    return captures


def is_valid_asin(supplier_sku):
    """True when ``supplier_sku`` is a syntactically valid Amazon ASIN."""
    return bool(ASIN_RE.match((supplier_sku or "").strip()))


def is_valid_hdsupply_part(supplier_sku):
    """True when ``supplier_sku`` is a valid (numeric) HD Supply part number."""
    return bool(HDSUPPLY_PART_RE.match((supplier_sku or "").strip()))


def build_order_pad(lines, *, header=("part#", "qty"), validate=None):
    """Build a vendor order-pad payload (CSV + copy block) from order lines.

    ``lines`` is an iterable of ``(name, supplier_sku, quantity)`` triples. The
    ``supplier_sku`` is the vendor's real part number (an ``ItemSupplier``
    field), which is what every distributor bulk order pad — Grainger,
    McMaster, Digi-Key, Amazon, Uline, MSC — keys on, so a plain ``part#,qty``
    list is the lowest-effort, highest-coverage ordering export.

    ``header`` sets the CSV header row so a supplier-specific format can reuse
    this builder (HD Supply's Saved-List upload wants ``Part Number,Quantity``).

    ``validate`` is an optional ``callable(cleaned_sku) -> bool``. When given, a
    non-blank SKU that fails it is collected in ``invalid_sku`` (and kept out of
    the pad) so a mis-typed part number is caught before it reaches a vendor
    cart. Without a validator every non-blank SKU is accepted and ``invalid_sku``
    is always empty.

    Returns a dict with:

    - ``csv``: a ``part#,qty`` CSV string including the header row.
    - ``text``: a plain ``part#\\tqty`` tab-separated copy-paste block
      (no header) suitable for pasting straight into an order pad.
    - ``line_count``: number of usable rows emitted.
    - ``missing_sku``: names of lines whose ``supplier_sku`` is blank/unusable.
    - ``invalid_sku``: names of lines whose ``supplier_sku`` failed ``validate``.

    Lines with a blank/whitespace-only or invalid ``supplier_sku`` are *not*
    written into the CSV/text — a row with no usable part number is junk a
    distributor pad rejects — but their names are collected in ``missing_sku`` /
    ``invalid_sku`` so the operator knows exactly what to fix. They are surfaced,
    never silently dropped.
    """
    usable = []
    missing_sku = []
    invalid_sku = []
    for name, supplier_sku, quantity in lines:
        cleaned = (supplier_sku or "").strip()
        if not cleaned:
            missing_sku.append(name)
        elif validate is not None and not validate(cleaned):
            invalid_sku.append(name)
        else:
            usable.append((cleaned, quantity))

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(list(header))
    for supplier_sku, quantity in usable:
        writer.writerow([supplier_sku, quantity])

    text = "\n".join(f"{supplier_sku}\t{quantity}" for supplier_sku, quantity in usable)

    return {
        "csv": buffer.getvalue(),
        "text": text,
        "line_count": len(usable),
        "missing_sku": missing_sku,
        "invalid_sku": invalid_sku,
    }


def build_amazon_cart(lines):
    """Build Amazon add-to-cart URL(s) from order lines (op-svpq).

    ``lines`` is an iterable of ``(name, supplier_sku, quantity)`` triples where
    ``supplier_sku`` must be an Amazon ASIN. Each valid line contributes an
    ``ASIN.{i}=<asin>&Quantity.{i}=<qty>`` pair (``i`` is 1-indexed *within each
    URL*). ASINs are ``[A-Z0-9]{10}`` and quantities are integers, so no value
    needs URL-encoding — the params are appended verbatim (plain GET, no signing).

    When a single URL would exceed :data:`AMAZON_URL_MAX_LEN`, the lines are
    chunked across multiple URLs (each restarting the index at 1). Lines whose
    ``supplier_sku`` is blank land in ``missing_sku``; lines whose SKU is not a
    valid ASIN land in ``invalid_sku`` — never silently dropped into a cart.

    Returns ``{cart_urls, line_count, missing_sku, invalid_sku}``.
    """
    valid = []
    missing_sku = []
    invalid_sku = []
    for name, supplier_sku, quantity in lines:
        cleaned = (supplier_sku or "").strip()
        if not cleaned:
            missing_sku.append(name)
        elif is_valid_asin(cleaned):
            valid.append((cleaned, quantity))
        else:
            invalid_sku.append(name)

    def render(chunk):
        params = []
        for index, (asin, quantity) in enumerate(chunk, start=1):
            params.append(f"ASIN.{index}={asin}")
            params.append(f"Quantity.{index}={quantity}")
        return AMAZON_CART_BASE + "?" + "&".join(params)

    cart_urls = []
    current = []
    for asin, quantity in valid:
        trial = current + [(asin, quantity)]
        # Start a new URL when appending this line would blow the length cap —
        # but only if the current chunk already has a line, so one oversized line
        # still gets its own URL rather than looping forever.
        if current and len(render(trial)) > AMAZON_URL_MAX_LEN:
            cart_urls.append(render(current))
            current = [(asin, quantity)]
        else:
            current = trial
    if current:
        cart_urls.append(render(current))

    return {
        "cart_urls": cart_urls,
        "line_count": len(valid),
        "missing_sku": missing_sku,
        "invalid_sku": invalid_sku,
    }


class ReorderRequestViewSet(viewsets.ModelViewSet):
    """
    API endpoint for reorder requests.

    Read/admin actions require JWT authentication. The ``create`` action is
    intentionally public so a member scanning a printed shelf QR code can
    submit a reorder without logging in — this is the primary flow the
    physical QR labels were built for.

    The ``create`` path accepts only the safe subset of fields exposed by
    :class:`ReorderRequestCreateSerializer` (item, quantity, requested_by,
    request_notes, priority) and the create response is also serialized
    with that limited shape so no admin metadata (cost, invoice,
    supplier URLs) ever leaks to an anonymous caller.
    """

    # Only JWT, no session auth needed
    authentication_classes = (JWTAuthentication,)
    permission_classes = [IsAuthenticated]
    queryset = (
        ReorderRequest.objects.select_related(
            "item", "item__category", "item__location", "item__count_level", "reviewed_by"
        )
        .prefetch_related(
            item_suppliers_prefetch("item__item_suppliers"),
            # ``item_details`` nests the full item serializer, which now carries
            # the packaging chain (op-hzji) — prefetch it so a page of requests
            # does not cost a query per row.
            "item__packaging_levels",
        )
        .all()
    )

    def get_permissions(self):
        """``create`` is public (QR-scan reorder); everything else stays
        gated to authenticated users so admin actions, list/retrieve, and
        sensitive analytics keep their lockdown."""
        if self.action == "create":
            return [AllowAny()]
        return super().get_permissions()

    def get_serializer_class(self):
        if self.action == "create":
            return ReorderRequestCreateSerializer
        return ReorderRequestSerializer

    @staticmethod
    def _is_reorder_approver(user, item) -> bool:
        """Return True when ``user`` may approve reorder requests for ``item``.

        The approver set is ``can_manage_sig_inventory`` — staff/superusers,
        Logistics, the item's SIG admins, and (for a space-owned item) any
        member who does not administer some other SIG. It is deliberately the
        same set on both sides of approval: whoever :meth:`approve` lets sign a
        request off is exactly who gets their own scan auto-approved by
        :meth:`_auto_approve_if_approver`, so the queue never parks a row
        waiting on a click from the person who raised it.
        """
        if not user or not user.is_authenticated:
            return False
        from membership.utils import can_manage_sig_inventory

        return can_manage_sig_inventory(user, item)

    @classmethod
    def _auto_approve_if_approver(cls, user, reorder):
        """Stamp ``reorder`` approved when whoever raised it could approve it.

        Anonymous QR scans and members who are not approvers for the item stay
        ``pending``. This is the single server-side create path for both the
        web scan flow and ScanTTY, so neither client needs to ask for it — nor
        could they: ``status`` is read-only on the create serializer.

        Returns True when the request was approved.
        """
        if not cls._is_reorder_approver(user, reorder.item):
            return False

        reorder.status = ReorderRequest.Status.APPROVED
        reorder.reviewed_by = user
        reorder.reviewed_at = timezone.now()
        reorder.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
        return True

    def create(self, request, *args, **kwargs):
        """Create a new reorder request.

        Public endpoint (see :meth:`get_permissions`). For authenticated
        users, applies the membership-level permission gate so a SIG
        member can't reorder for another SIG's inventory. Anonymous
        callers go straight to the serializer — the serializer only
        exposes safe fields, so they cannot smuggle cost / supplier-URL
        data into the row.

        A scan raised by someone who could approve it is approved on the
        spot (:meth:`_auto_approve_if_approver`).
        """
        user = request.user
        if user.is_authenticated:
            item_id = request.data.get("item")
            if item_id:
                try:
                    from inventory.models import InventoryItem
                    from membership.utils import can_create_reorder_request

                    item = InventoryItem.objects.get(pk=item_id)
                    if not can_create_reorder_request(user, item):
                        return Response(
                            {
                                "detail": "You do not have permission to create reorder requests for this item."
                            },
                            status=status.HTTP_403_FORBIDDEN,
                        )
                except InventoryItem.DoesNotExist:
                    pass  # Let serializer handle validation

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)

        self._auto_approve_if_approver(user, serializer.instance)

        # Anonymous callers get the limited create-serializer shape back
        # so no admin metadata (admin_notes, invoice_url, supplier_url,
        # actual_cost, …) leaks even by accident. Authenticated callers
        # keep the existing richer response.
        instance = ReorderRequest.objects.get(id=serializer.instance.id)
        if user.is_authenticated:
            output_serializer = ReorderRequestSerializer(
                instance, context=self.get_serializer_context()
            )
        else:
            output_serializer = ReorderRequestCreateSerializer(instance)

        # Create notifications for admins about new reorder request
        try:
            from notifications.services import notify_admins

            item = instance.item
            notify_admins(
                type="info",
                title="New Reorder Request",
                message=f"New reorder request for {item.name} (quantity: {instance.quantity})",
                action_url="/inventory/admin",
                metadata={
                    "reorder_request_id": instance.id,
                    "item_id": str(item.id),
                    "priority": instance.priority,
                },
            )
        except Exception:  # nosec B110
            # Don't fail the request if notification creation fails
            pass

        return Response(output_serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    @action(detail=False, methods=["get"])
    def pending(self, request):
        """Get all pending reorder requests."""
        try:
            # Return all pending requests without pagination for admin dashboard
            # Use the base queryset to ensure all prefetching is maintained
            pending = self.queryset.filter(status=ReorderRequest.Status.PENDING).order_by(
                "-priority", "requested_at"
            )

            # Filter by SIG ownership (list policy: staff/super/Logistics and
            # regular users see all pending requests; SIG admins see only
            # requests for their SIGs' inventory).
            from membership.services import OwnershipVisibility, scope_queryset_by_ownership

            pending = scope_queryset_by_ownership(
                pending,
                request.user,
                policy=OwnershipVisibility.LIST,
                field="item__owning_group",
            )

            serializer = self.get_serializer(pending, many=True)
            return Response(serializer.data)
        except AttributeError as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.exception(
                "AttributeError in pending endpoint (likely missing supplier data): %s", str(e)
            )
            return Response(
                {
                    "detail": "Error serializing data. Some items may be missing supplier information."
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.exception("Error in pending endpoint: %s", str(e))
            return Response(
                {"detail": f"An error occurred: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated])
    def sig_pending(self, request):
        """Get pending reorder requests for SIGs the user administers."""
        from membership.utils import get_user_managed_sigs

        user = request.user
        user_sigs = get_user_managed_sigs(user)

        if not user_sigs.exists():
            return Response(
                {"detail": "You are not an admin of any SIGs."},
                status=status.HTTP_403_FORBIDDEN,
            )

        pending = self.queryset.filter(
            status=ReorderRequest.Status.PENDING, item__owning_group__in=user_sigs
        ).order_by("-priority", "requested_at")
        serializer = self.get_serializer(pending, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated])
    def by_supplier(self, request):
        """Group pending requests by supplier for easier bulk ordering.

        ``total_estimated_cost`` is the sum of the requests this group COULD
        price, and ``unpriced_item_count`` / ``estimated_total_is_partial`` say
        how many it could not — the same shape ``create_optimized_order`` and
        ``reorder_data`` carry, and for the same reason (op-9m2v): the admin
        dashboard renders this number as a bulk-ordering total, and a request
        for an item nobody has priced contributed nothing to it and said
        nothing about itself. The NUMBER is unchanged — an unpriced request
        added nothing before and adds nothing now, and a free one adds its
        honest ``0.00`` either way.
        """
        from inventory.services.pricing import PriceRollup, order_unit_price

        pending = (
            ReorderRequest.objects.filter(status=ReorderRequest.Status.PENDING)
            .select_related("item", "item__count_level")
            .prefetch_related(
                item_suppliers_prefetch("item__item_suppliers"), "item__packaging_levels"
            )
        )

        # Group by supplier
        suppliers = {}
        rollups = {}
        # Built ONCE, not per row: ``item_details.supplier_choice`` decides which
        # audience it serves from ``context["request"]`` and fails closed, so a
        # hand-built serializer without it hands an operator the anonymous view.
        serializer_context = self.get_serializer_context()
        for req in pending:
            supplier_name = req.item.supplier.name if req.item.supplier else "No Supplier"
            supplier_type = req.item.supplier.supplier_type if req.item.supplier else "other"

            if supplier_name not in suppliers:
                suppliers[supplier_name] = {
                    "supplier": supplier_name,
                    "supplier_type": supplier_type,
                    "requests": [],
                    "total_estimated_cost": 0,
                    "item_count": 0,
                }
                rollups[supplier_name] = PriceRollup()

            suppliers[supplier_name]["requests"].append(
                ReorderRequestSerializer(req, context=serializer_context).data
            )
            suppliers[supplier_name]["item_count"] += 1
            rollups[supplier_name].add(order_unit_price(req.item), req.quantity)

        for supplier_name, group in suppliers.items():
            rollup = rollups[supplier_name]
            group["total_estimated_cost"] = float(rollup.amount)
            group["unpriced_item_count"] = rollup.unpriced_count
            group["estimated_total_is_partial"] = not rollup.is_complete

        return Response(list(suppliers.values()))

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def approve(self, request, pk=None):
        """Approve a reorder request.

        Restricted to approvers for the request's item
        (:meth:`_is_reorder_approver`) — until op-tm70 any authenticated
        member could sign off any request, including their own, which made
        approval a formality rather than a gate. Non-approvers get a 403.
        """
        reorder = self.get_object()
        if not self._is_reorder_approver(request.user, reorder.item):
            return Response(
                {"detail": "You do not have permission to approve reorder requests for this item."},
                status=status.HTTP_403_FORBIDDEN,
            )

        reorder.status = ReorderRequest.Status.APPROVED
        reorder.reviewed_by = request.user
        reorder.reviewed_at = timezone.now()
        reorder.admin_notes = request.data.get("admin_notes", reorder.admin_notes)
        reorder.save()

        serializer = self.get_serializer(reorder)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def mark_ordered(self, request, pk=None):
        """Mark a request as ordered.

        The order/PO number lives in the Purchase Order domain and is carried
        onto the request automatically when a PO is created/finalized, so it is
        not required here — marking ordered is a one-click action. Any of
        ``order_number``, ``estimated_delivery`` or ``actual_cost`` may still be
        supplied optionally, but fields that are omitted are left untouched so a
        bare mark-ordered never wipes values a PO already populated.
        """
        reorder = self.get_object()
        reorder.status = ReorderRequest.Status.ORDERED
        reorder.ordered_at = timezone.now()

        if "order_number" in request.data:
            reorder.order_number = request.data.get("order_number", "")
        if "estimated_delivery" in request.data:
            reorder.estimated_delivery = request.data.get("estimated_delivery")
        if "actual_cost" in request.data:
            reorder.actual_cost = request.data.get("actual_cost")

        if not reorder.reviewed_by:
            reorder.reviewed_by = request.user
            reorder.reviewed_at = timezone.now()

        reorder.save()

        serializer = self.get_serializer(reorder)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def mark_received(self, request, pk=None):
        """Mark a request as received and update inventory.

        The stock bump only happens on the transition *into* ``received``, so
        the action is idempotent: re-posting to an already-received request is
        a no-op that returns it unchanged rather than adding ``quantity`` to
        inventory a second time. That covers a double-click, a stale browser
        tab, and a request already closed by a purchase-order receipt (which
        moves the stock itself). A cancelled request cannot be received.

        The guard re-reads the row under ``select_for_update`` inside the
        transaction that also writes the stock, so two clicks in flight at once
        can't both pass it.
        """
        reorder = self.get_object()

        with transaction.atomic():
            reorder = ReorderRequest.objects.select_for_update().get(pk=reorder.pk)

            if reorder.status == ReorderRequest.Status.RECEIVED:
                return Response(self.get_serializer(reorder).data)

            if reorder.status == ReorderRequest.Status.CANCELLED:
                return Response(
                    {"detail": "Cannot receive a cancelled reorder request."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            reorder.status = ReorderRequest.Status.RECEIVED
            reorder.actual_delivery = request.data.get("actual_delivery", timezone.now().date())
            reorder.save()

            # Update inventory stock
            item = reorder.item
            item.current_stock += reorder.quantity
            item.save()

        serializer = self.get_serializer(reorder)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def cancel(self, request, pk=None):
        """Cancel a reorder request."""
        reorder = self.get_object()
        reorder.status = ReorderRequest.Status.CANCELLED
        reorder.reviewed_by = request.user
        reorder.reviewed_at = timezone.now()
        reorder.admin_notes = request.data.get("admin_notes", reorder.admin_notes)
        reorder.save()

        serializer = self.get_serializer(reorder)
        return Response(serializer.data)

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated])
    def generate_cart_links(self, request):
        """Group the live reorder queue by supplier into per-supplier order pads.

        For every *approved* reorder request whose item has a supplier
        relationship, emit a ``part#,qty`` order pad (CSV +
        tab-separated copy block) built from the item's supplier SKU and
        requested quantity — the same vendor-agnostic builder the PO
        ``export_order`` action uses (DRY). The operator pastes or uploads each
        supplier's block into that distributor's bulk order pad.

        Replaces the previous dead implementation, which branched on
        ``supplier_type`` values (``amazon``/``grainger``/``hdsupply``) that the
        ``Supplier`` model never produces — its types are ``local``/``online``/
        ``national`` — so it always returned ``{}``.

        Approved-only since op-tm70: an order pad is a shopping list about to
        be sent to a vendor, so an unapproved ask must not appear on it.
        """
        approved_requests = (
            ReorderRequest.objects.filter(status__in=services.PO_ELIGIBLE_STATUSES)
            .select_related("item", "item__count_level")
            .prefetch_related(
                item_suppliers_prefetch("item__item_suppliers"), "item__packaging_levels"
            )
        )

        # Group each request under its item's primary supplier so the emitted
        # SKU and the supplier heading always come from the same ItemSupplier.
        grouped = {}
        for req in approved_requests:
            link = req.item.primary_item_supplier
            if link is None:
                continue
            grouped.setdefault(link.supplier.name, []).append(
                (req.item.name, link.supplier_sku, req.quantity)
            )

        cart_data = {}
        for supplier_name, lines in grouped.items():
            payload = build_order_pad(lines)
            cart_data[supplier_name] = {
                "supplier": supplier_name,
                "csv": payload["csv"],
                "text": payload["text"],
                "line_count": payload["line_count"],
            }

        return Response(cart_data)


class PurchaseOrderViewSet(viewsets.ModelViewSet):
    """API endpoint for purchase order management."""

    queryset = PurchaseOrder.objects.select_related(
        # op-yoos: supplier_agreement feeds
        # PurchaseOrderSerializer.supplier_agreement_details — joined here so
        # the list endpoint does not fire one extra query per order.
        "supplier",
        "created_by",
        "sent_by",
        "supplier_agreement",
        # op-shb9: the order-level work-order / committee associations feed
        # PurchaseOrderSerializer.{work_order,owning_group}_details. Joined for
        # the same reason as supplier_agreement above.
        "work_order",
        "work_order__maintenance_item",
        "work_order__asset",
        "owning_group",
    ).prefetch_related(
        "items__item_supplier__item",
        "items__item_supplier__supplier",
        "items__asset",
        "items__asset__manufacturer",
        # op-bu80: feed PurchaseOrderItemSerializer.work_order_details ("ordered
        # for this job") from one query rather than one per tagged line.
        # ``display_title`` falls back template -> reported problem -> asset,
        # so all three are pulled alongside it.
        "items__work_order__maintenance_item",
        "items__work_order__asset",
        "items__work_order__asset_problems",
        # op-shb9: the reverse-FK leg of the order-level work order's
        # display_title, plus the per-line committee block.
        "work_order__asset_problems",
        "items__owning_group",
        "deliveries__items",
        "attachments__uploaded_by",
    )

    def get_permissions(self):
        """
        Allow public access for viewing active and settled purchase orders.
        Require authentication for creating, updating, or viewing draft/cancelled orders.
        """
        if self.action in ["list", "retrieve"]:
            return [AllowAny()]
        return [IsAuthenticated()]

    def get_queryset(self):
        """
        For public access, only show active and settled orders.
        For authenticated users, show all orders.
        """
        queryset = super().get_queryset()

        # If user is not authenticated, only show active and settled orders
        if not self.request.user.is_authenticated:
            queryset = queryset.filter(
                status__in=[
                    PurchaseOrder.Status.SENT,
                    PurchaseOrder.Status.CONFIRMED,
                    PurchaseOrder.Status.PARTIALLY_RECEIVED,
                    PurchaseOrder.Status.RECEIVED,
                ]
            )

        # Apply status filter if provided
        status_filter = self.request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        # Search functionality
        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(po_number__icontains=search)
                | Q(supplier__name__icontains=search)
                | Q(notes__icontains=search)
            )

        # Hide from the list an order that was EMPTIED BY VOIDING AFTER IT LEFT
        # THE SHOP, and only that (oms-a8o). Such an order HAS line items, every
        # one of them has been struck off, and it once carried a real
        # obligation: it has nothing left to show or pay for, which is the whole
        # of the rationale this filter has ever had. Detail retrieval is
        # unaffected so deep links and audit trails still resolve.
        #
        # THE THIRD CLAUSE IS DERIVED, NOT A STATUS LIST. "Nothing to pay for"
        # presupposes something that was owed, and nothing is owed while the
        # order is still the shop's own private document — striking a line off
        # a draft is the operator editing their own work, which is the very act
        # line DELETION replaced. So the boundary is
        # ``PurchaseOrder.PRE_SUPPLIER_STATUSES``, read off the order's own
        # state machine, the same set ``assert_addable`` / ``assert_deletable``
        # / ``can_delete_items`` read and for the same reason. A second pre-send
        # status is one edit, there and here. This is reachable rather than
        # theoretical: ``void_item`` carries NO status gate, so voiding the only
        # line of a draft is a live second route into the same trap that
        # deleting it was.
        #
        # AN ORDER WITH NO LINE ITEMS AT ALL IS NOT THAT, and is not hidden.
        # The two used to be one condition, because "no line is active" is
        # vacuously true of an order that has no lines — but such an order has
        # not discharged an obligation, it has not taken one on yet. Creation
        # refuses an empty ``items`` list, so this is the order whose lines were
        # DELETED: "delete the wrong line, then add the right one" is the
        # workflow line deletion exists for (oms-po-line-delete), and it made
        # the operator's own draft vanish mid-edit. Detail retrieval staying
        # unfiltered is no answer when the link is the thing you no longer
        # have. The emptiness that hides an order is therefore spelled out as
        # what it is — lines exist, none survive — rather than inferred from a
        # count that cannot tell the two apart.
        #
        # No status NAME appears in the condition: the pre-send clause reads
        # ``PRE_SUPPLIER_STATUSES`` off the order's own state machine, the same
        # set the three line-editing sites read, and the zero-line clause is
        # the same sentence in every status. So a second pre-send status is one
        # edit to that frozenset and none in any of the four places that read
        # it, this one included; ``tests/test_po_list_emptiness.py`` crosses
        # both axes rather than naming statuses here.
        if self.action == "list":
            queryset = queryset.annotate(
                _items_count=Count("items", distinct=True),
                _active_items_count=Count("items", filter=Q(items__is_voided=False), distinct=True),
            ).filter(
                Q(_items_count=0)
                | Q(_active_items_count__gt=0)
                | Q(status__in=PurchaseOrder.PRE_SUPPLIER_STATUSES)
            )

        return queryset

    def get_serializer_class(self):
        if self.action == "create":
            return PurchaseOrderCreateSerializer
        return PurchaseOrderSerializer

    def perform_create(self, serializer):
        purchase_order = serializer.save()
        record_audit_event(
            action=PurchaseOrderAuditEvent.Action.PO_CREATE,
            actor=self.request.user,
            purchase_order=purchase_order,
            metadata={
                "supplier_id": purchase_order.supplier_id,
                "po_number": purchase_order.po_number,
            },
        )
        # A PO created with a sales order number is treated as already
        # submitted to the supplier (oms-qdxss). A fresh PO is always DRAFT, so
        # a non-empty sales_order_number is the empty -> non-empty trigger.
        if self._has_sales_order_number(purchase_order):
            self._auto_transition_to_sent(purchase_order)

    def perform_update(self, serializer):
        # Capture the pre-update value so we only auto-send on the empty ->
        # non-empty edge. Editing other fields, or clearing the number, must
        # never change status (oms-qdxss).
        had_sales_order_number = self._has_sales_order_number(serializer.instance)
        purchase_order = serializer.save()
        if not had_sales_order_number and self._has_sales_order_number(purchase_order):
            self._auto_transition_to_sent(purchase_order)

    @staticmethod
    def _has_sales_order_number(purchase_order):
        """True when the PO carries a non-blank sales order number.

        Whitespace-only values are treated as empty so they never trigger the
        auto-send transition (oms-qdxss).
        """
        return bool((purchase_order.sales_order_number or "").strip())

    def _auto_transition_to_sent(self, purchase_order):
        """Auto-move a DRAFT PO to SENT when a sales order number is attached.

        Idempotent: a no-op unless the PO is currently DRAFT, so attaching a
        number to an already-SENT/confirmed/received PO never re-stamps or
        downgrades it. Wrapped defensively so a failure in the transition never
        breaks the create/update that triggered it (oms-qdxss).
        """
        if purchase_order.status != PurchaseOrder.Status.DRAFT:
            return
        try:
            self._mark_sent(purchase_order, self.request.user)
        except Exception:  # pragma: no cover - defensive: never break the write
            import logging

            logging.getLogger(__name__).exception(
                "Auto-transition to SENT failed for PO %s", purchase_order.pk
            )

    def _mark_sent(self, purchase_order, user):
        """Stamp a purchase order as SENT and record the transition.

        Shared by the manual ``send_to_supplier`` action and the automatic
        sales-order-number trigger so both paths stay consistent: status -> SENT,
        ``sent_by``/``sent_at`` stamped, the linked reorder requests synced (both
        via :func:`services.mark_sent`), and a ``po_send`` audit event recorded.
        Callers own the DRAFT precondition.
        """
        services.mark_sent(purchase_order, user)
        record_audit_event(
            action=PurchaseOrderAuditEvent.Action.PO_SEND,
            actor=user,
            purchase_order=purchase_order,
            metadata={"po_number": purchase_order.po_number},
        )

    @action(detail=False, methods=["post"])
    def create_optimized_order(self, request):
        """Create an optimized purchase order based on current needs and supplier analysis."""

        # Get items that need reordering (retired items are phased out and
        # excluded — no optimized order line for them). ``low_stock_q`` compares
        # stock to the reorder point at the granularity each item is counted in;
        # for the ``each`` items that is exactly the old
        # ``current_stock <= minimum_stock`` (op-es7c).
        low_stock_items = (
            # Kits are never action rows (op-8n0): a kit holds no stock, so it
            # is bought as the ANSWER to a low component, never as a low item.
            # Filtered caller-side beside ``is_retired`` rather than folded into
            # ``low_stock_q`` -- that query is asserted to be the exact twin of
            # ``InventoryItem.needs_reorder``, and the convention is that
            # visibility rules live at the call site.
            InventoryItem.objects.filter(low_stock_q(), is_retired=False, is_kit=False)
            .select_related("category", "location", "count_level")
            .prefetch_related(item_suppliers_prefetch())
        )

        if not low_stock_items.exists():
            return Response(
                {"message": "No items currently need reordering"},
                status=status.HTTP_200_OK,
            )

        # Group items by optimal supplier
        supplier_groups = {}
        recommendations = []

        for item in low_stock_items:
            # Find the best supplier for this item
            best_supplier = self._find_best_supplier(item)

            if best_supplier:
                supplier_id = best_supplier.supplier.id

                if supplier_id not in supplier_groups:
                    supplier_groups[supplier_id] = {
                        "supplier": best_supplier.supplier,
                        "items": [],
                        "rollup": PriceRollup(),
                    }

                # Calculate optimal quantity (considering package sizes)
                optimal_qty = self._calculate_optimal_quantity(item, best_supplier)

                # What this vendor charges, through the ONE price derivation
                # (op-9m2v). ``unit_price_of`` keeps a recorded 0.00 (donated
                # stock, a free sample) apart from a column nobody filled in,
                # which ``unit_cost or 0`` could not: both produced the same
                # confident $0.00 line and the same understated order total.
                unit_price = unit_price_of(best_supplier)
                package_price = package_price_of(best_supplier)
                line_total = supplier_groups[supplier_id]["rollup"].add(unit_price, optimal_qty)

                supplier_groups[supplier_id]["items"].append(
                    {
                        "item_id": item.id,
                        "item_name": item.name,
                        "item_supplier_id": best_supplier.id,
                        "current_stock": item.current_stock,
                        "minimum_stock": item.minimum_stock,
                        "recommended_quantity": optimal_qty,
                        "unit_cost": unit_price.amount,
                        "package_cost": package_price.amount,
                        "quantity_per_package": best_supplier.quantity_per_package,
                        # ``null``, not 0, when this vendor has no price on
                        # file — with the cause and the remedy beside it, so a
                        # blank row tells the purchaser which screen to fix it
                        # on rather than merely refusing to say a number.
                        "estimated_line_total": line_total,
                        "unit_cost_state": unit_price.state,
                        "unit_cost_detail": explain(
                            unit_price,
                            item_name=item.name,
                            supplier_name=best_supplier.supplier.name,
                        ),
                        # Count-level presentation (op-ev14); the quantities
                        # above remain base units.
                        "count_unit": count_unit(item),
                        "on_hand_display": on_hand_display(item),
                        "recommended_quantity_at_unit": (
                            optimal_qty // item.count_level.base_units
                            if counts_in_packs(item)
                            else optimal_qty
                        ),
                    }
                )

        # Prepare recommendations for review. Every total below is the sum of
        # the lines this endpoint COULD price, and says how many it could not
        # (op-9m2v): base summed an unpriced line as $0.00, so a recommendation
        # read as complete while understating what the order would actually
        # cost. ``estimated_total_is_partial`` is the claim a screen is allowed
        # to make about the number beside it.
        overall = PriceRollup()
        for supplier_id, group in supplier_groups.items():
            rollup = group["rollup"]
            overall.absorb(rollup)
            recommendations.append(
                {
                    "supplier_id": supplier_id,
                    "supplier_name": group["supplier"].name,
                    "supplier_type": group["supplier"].supplier_type,
                    "total_items": len(group["items"]),
                    "estimated_total": rollup.amount,
                    "unpriced_item_count": rollup.unpriced_count,
                    "estimated_total_is_partial": not rollup.is_complete,
                    "items": group["items"],
                }
            )

        # Sort by estimated total (largest orders first)
        recommendations.sort(key=lambda x: x["estimated_total"], reverse=True)

        return Response(
            {
                "recommendations": recommendations,
                "total_suppliers": len(recommendations),
                "total_estimated_cost": overall.amount,
                "unpriced_item_count": overall.unpriced_count,
                "total_estimated_cost_is_partial": not overall.is_complete,
                "message": "Order recommendations generated. Review and confirm to create purchase orders.",
            }
        )

    @action(detail=False, methods=["get"])
    def reorder_data(self, request):
        """
        Get data for PO creation: items with approved reorder requests
        prioritized, then low stock items grouped by supplier with pricing.

        Returns all suppliers that have items needing reorder, along with
        assets where the supplier is the manufacturer.

        Approval gates this pad (op-tm70): a ``pending`` request is an ask
        nobody has signed off yet, so it must not prefill a purchase order.
        An item whose only request is pending is not dropped from the pad —
        it simply falls through to the low-stock half below (if its stock
        warrants it) with no request attached, exactly like an item that was
        never scanned.
        """
        from inventory.models import Asset, Supplier

        # First, get items with an approved reorder request.
        # Retired items are phased out and must never appear in the reorder data,
        # even if a request lingered from before they were retired.
        items_with_requests = (
            InventoryItem.objects.filter(
                reorder_requests__status__in=services.PO_ELIGIBLE_STATUSES,
                is_active=True,
                is_retired=False,
                # This half filters on REQUEST STATUS, not stock, so the
                # ``is_kit`` guard on the low-stock half below does not cover
                # it -- a stale request against a kit would still surface here
                # (op-8n0).
                is_kit=False,
            )
            .distinct()
            .select_related("category", "location", "count_level")
            .prefetch_related(item_suppliers_prefetch(), services.approved_requests_prefetch())
        )

        # Also get items that need reordering (stock at/below the reorder point,
        # compared at whatever granularity each item is counted in — op-es7c)
        # but exclude those already in items_with_requests
        low_stock_items = (
            InventoryItem.objects.filter(
                low_stock_q(),
                is_active=True,
                is_retired=False,
                is_kit=False,
            )
            .exclude(id__in=items_with_requests.values_list("id", flat=True))
            .select_related("category", "location", "count_level")
            .prefetch_related(item_suppliers_prefetch(), services.approved_requests_prefetch())
        )

        # Combine both sets, prioritizing items with requests
        all_items = list(items_with_requests) + list(low_stock_items)

        # Build supplier data with their available items
        supplier_data = {}
        # Low items that no supplier group below can carry, and why. Without
        # this they simply do not appear: ``total_low_stock_items`` counts them
        # and no pad offers them, which reads as "already handled" rather than
        # "you cannot order this" (op-2rsp). A refusal an operator can act on
        # has to name the item and the remedy.
        unorderable_items = []

        for item in all_items:
            # Get all active, non-discontinued suppliers for this item
            item_suppliers = item.item_suppliers.filter(
                is_active=True,
                is_discontinued=False,
            ).select_related("supplier")

            choice = select_supplier(item)
            if not choice:
                unorderable_items.append(
                    {
                        "item_id": str(item.id),
                        "item_name": item.name,
                        "item_sku": item.sku,
                        "reason": choice.reason,
                        "detail": (
                            f"No supplier is linked to {item.name}. Add one on the "
                            "item before it can go on an order."
                            if choice.reason == NO_SUPPLIERS
                            else (
                                f"Every supplier link for {item.name} is inactive or "
                                "discontinued. Reactivate one, or add a supplier that "
                                "still carries it."
                            )
                        ),
                    }
                )

            for item_supplier in item_suppliers:
                supplier = item_supplier.supplier
                supplier_id = supplier.id

                if supplier_id not in supplier_data:
                    supplier_data[supplier_id] = {
                        "id": supplier_id,
                        "name": supplier.name,
                        "supplier_type": supplier.supplier_type,
                        "website": supplier.website,
                        "items": [],
                        "assets": [],
                        # Kits that would restock one of this supplier's low
                        # components (op-8n0). Informational, never an action
                        # row -- always present so the PO form can read it
                        # unconditionally.
                        "kits": [],
                        "total_items": 0,
                        # The group's money, and what it could not price
                        # (op-9m2v) — see the rollup read-out below.
                        "rollup": PriceRollup(),
                        "avg_lead_time": 0,
                    }

                # Calculate suggested quantity (base units, mode-aware — op-es7c)
                # If item has an approved reorder request, use that quantity.
                # Read through the service, NOT ``item.get_active_reorder_request()``:
                # that one deliberately still counts pending/ordered for the
                # inventory "active reorder" badge, and using it here is what let
                # unapproved asks prefill a PO (op-tm70).
                approved_request = services.get_approved_reorder_request(item)
                if approved_request:
                    suggested_qty = approved_request.quantity
                else:
                    suggested_qty = base_reorder_quantity(item)

                # Adjust for package quantities. Supplier packaging only rounds
                # items counted in base units: a pack-counting item already
                # ordered whole packs of its own chain above, and re-rounding to
                # a supplier's case would silently inflate that. "Does this
                # vendor declare a case?" comes from the ONE pack-size
                # derivation (op-c1ke); the rounding is unchanged.
                declared_case = None if counts_in_packs(item) else declares_a_case(item_supplier)
                if declared_case is not None:
                    packages_needed = (suggested_qty + declared_case - 1) // declared_case
                    suggested_qty = packages_needed * declared_case

                # What this vendor charges, through the ONE price derivation
                # (op-9m2v). Base's ``unit_cost or Decimal("0.00")`` costed an
                # unpriced line at nothing and added that nothing to the pad's
                # ``estimated_total``, so the purchaser saw a complete-looking
                # total that the invoice would then exceed. A recorded 0.00 is
                # still a price and still lands on the line.
                unit_price = unit_price_of(item_supplier)
                package_price = package_price_of(item_supplier)
                line_total = supplier_data[supplier_id]["rollup"].add(unit_price, suggested_qty)

                # Flag the line as request-driven. The API key keeps its
                # ``has_active_reorder_request`` name (clients read it), but
                # since op-tm70 it means "has an APPROVED request".
                has_active_request = approved_request is not None
                reorder_request_id = approved_request.id if approved_request else None

                supplier_data[supplier_id]["items"].append(
                    {
                        "item_supplier_id": item_supplier.id,
                        "item_id": str(item.id),
                        "item_name": item.name,
                        "item_sku": item.sku,
                        "current_stock": item.current_stock,
                        "minimum_stock": item.minimum_stock,
                        "reorder_quantity": item.reorder_quantity,
                        "suggested_quantity": suggested_qty,
                        # ``null`` where the price is unknown; a string where
                        # it is known, ``"0.00"`` included. The client must not
                        # render an unknown as $0.00 — ``unit_cost_detail``
                        # carries the sentence that says what to do instead.
                        "unit_cost": None if not unit_price else str(unit_price.amount),
                        "unit_cost_state": unit_price.state,
                        "unit_cost_detail": explain(
                            unit_price,
                            item_name=item.name,
                            supplier_name=supplier.name,
                        ),
                        "package_cost": (None if not package_price else str(package_price.amount)),
                        "quantity_per_package": item_supplier.quantity_per_package,
                        "lead_time_days": item_supplier.average_lead_time,
                        "supplier_sku": item_supplier.supplier_sku,
                        "supplier_url": item_supplier.supplier_url,
                        "is_primary": item_supplier.is_primary,
                        "line_total": None if line_total is None else str(line_total),
                        "has_active_reorder_request": has_active_request,
                        "reorder_request_id": reorder_request_id,
                        # Presentation at the item's counting granularity
                        # (op-ev14): every number above stays base units, these
                        # let the pad label "2 cases on hand" without the client
                        # re-deriving it. ``count_level`` is select_related on
                        # both querysets feeding this loop, so they are free.
                        "count_unit": count_unit(item),
                        "on_hand_display": on_hand_display(item),
                        "reorder_display": reorder_display(item),
                        "suggested_quantity_at_unit": (
                            suggested_qty // item.count_level.base_units
                            if counts_in_packs(item)
                            else suggested_qty
                        ),
                    }
                )

                supplier_data[supplier_id]["total_items"] += 1

        # Kits that would restock a low component (op-8n0). "Show, don't act":
        # a purchaser looking at low cyan ink sees that the Eufy Ink Kit is one
        # way to buy it, and decides for themselves. Deliberately NOT an action
        # row -- kits are excluded from ``all_items`` above, and nothing here
        # touches ``total_items`` or ``estimated_total``.
        #
        # This loop SEEDS ``supplier_data`` rather than iterating it, unlike the
        # assets loop below. That is load-bearing: a supplier whose only reason
        # to appear is a low kit component has no low items and no assets, so
        # iterating the existing keys would drop it silently -- which is exactly
        # the case this feature exists for.
        low_component_ids = {item.id for item in all_items}
        if low_component_ids:
            supplying_kits = (
                InventoryItem.objects.filter(
                    is_kit=True,
                    is_active=True,
                    kit_components__component_id__in=low_component_ids,
                )
                .distinct()
                .prefetch_related("kit_components__component", item_suppliers_prefetch())
                .order_by("name")
            )
            for kit in supplying_kits:
                components = [
                    {
                        "id": row.component_id,
                        "name": row.component.name,
                        "sku": row.component.sku,
                        "quantity_per_kit": row.quantity,
                        "is_low": row.component_id in low_component_ids,
                    }
                    for row in sorted(kit.kit_components.all(), key=lambda r: r.component.name)
                ]
                for item_supplier in kit.item_suppliers.all():
                    if not item_supplier.is_active or item_supplier.is_discontinued:
                        continue
                    supplier = item_supplier.supplier
                    if supplier.id not in supplier_data:
                        supplier_data[supplier.id] = {
                            "id": supplier.id,
                            "name": supplier.name,
                            "supplier_type": supplier.supplier_type,
                            "website": supplier.website,
                            "items": [],
                            "assets": [],
                            "kits": [],
                            "total_items": 0,
                            "rollup": PriceRollup(),
                            "avg_lead_time": 0,
                        }
                    # A kit row is informational and never an action row, so it
                    # touches no total — but it is still a price on a screen,
                    # and an unknown one must not read as free (op-9m2v).
                    kit_price = unit_price_of(item_supplier)
                    supplier_data[supplier.id].setdefault("kits", []).append(
                        {
                            "id": kit.id,
                            "name": kit.name,
                            "sku": kit.sku,
                            "supplier_sku": item_supplier.supplier_sku,
                            "unit_cost": None if not kit_price else str(kit_price.amount),
                            "item_supplier_id": item_supplier.id,
                            "components": components,
                            "low_component_count": sum(
                                1 for component in components if component["is_low"]
                            ),
                        }
                    )

        # Add assets for each supplier (where supplier is the manufacturer)
        for supplier_id, data in supplier_data.items():
            assets = Asset.objects.filter(
                manufacturer_id=supplier_id,
                is_active=True,
                status__in=[
                    Asset.Status.ACTIVE,
                    Asset.Status.MAINTENANCE,
                    Asset.Status.IMPLEMENTING,
                    Asset.Status.TESTING,
                ],
            ).values("id", "name", "asset_tag", "serial_number", "product_url")

            data["assets"] = [
                {
                    "id": str(asset["id"]),
                    "name": asset["name"],
                    "asset_tag": asset["asset_tag"],
                    "serial_number": asset["serial_number"],
                    "product_url": asset["product_url"],
                }
                for asset in assets
            ]

            # Calculate average lead time for supplier
            lead_times = [
                item["lead_time_days"] for item in data["items"] if item["lead_time_days"]
            ]
            data["avg_lead_time"] = sum(lead_times) / len(lead_times) if lead_times else 0
            # The group's money, read off the rollup rather than accumulated
            # into the payload dict: ``estimated_total`` is the sum of the
            # lines this pad COULD price and ``unpriced_item_count`` is how
            # many it could not, so a partial total says so instead of looking
            # complete (op-9m2v).
            rollup = data.pop("rollup")
            data["estimated_total"] = str(rollup.amount)
            data["unpriced_item_count"] = rollup.unpriced_count
            data["estimated_total_is_partial"] = not rollup.is_complete

        # Also include suppliers that have assets but no low-stock items
        suppliers_with_assets = (
            Supplier.objects.filter(
                manufactured_assets__is_active=True,
                manufactured_assets__status__in=[
                    Asset.Status.ACTIVE,
                    Asset.Status.MAINTENANCE,
                    Asset.Status.IMPLEMENTING,
                    Asset.Status.TESTING,
                ],
            )
            .exclude(id__in=supplier_data.keys())
            .distinct()
        )

        for supplier in suppliers_with_assets:
            assets = Asset.objects.filter(
                manufacturer=supplier,
                is_active=True,
                status__in=[
                    Asset.Status.ACTIVE,
                    Asset.Status.MAINTENANCE,
                    Asset.Status.IMPLEMENTING,
                    Asset.Status.TESTING,
                ],
            ).values("id", "name", "asset_tag", "serial_number", "product_url")

            supplier_data[supplier.id] = {
                "id": supplier.id,
                "name": supplier.name,
                "supplier_type": supplier.supplier_type,
                "website": supplier.website,
                "items": [],
                "assets": [
                    {
                        "id": str(asset["id"]),
                        "name": asset["name"],
                        "asset_tag": asset["asset_tag"],
                        "serial_number": asset["serial_number"],
                        "product_url": asset["product_url"],
                    }
                    for asset in assets
                ],
                "kits": [],
                "total_items": 0,
                # No items at all, so nothing to price and nothing omitted:
                # "$0.00 of items" is a true statement about an assets-only
                # group, not a fabricated one.
                "estimated_total": "0.00",
                "unpriced_item_count": 0,
                "estimated_total_is_partial": False,
                "avg_lead_time": 0,
            }

        # Convert to list and sort by estimated total (highest first)
        suppliers_list = sorted(
            supplier_data.values(),
            key=lambda x: Decimal(x["estimated_total"]),
            reverse=True,
        )

        return Response(
            {
                "suppliers": suppliers_list,
                "total_suppliers": len(suppliers_list),
                "total_low_stock_items": len(all_items),
                "items_with_requests": items_with_requests.count(),
                # Additive (op-2rsp): existing clients that only read
                # ``suppliers`` are unaffected, and one that wants to warn the
                # operator now has the list to warn about.
                "items_without_orderable_supplier": unorderable_items,
            }
        )

    def _find_best_supplier(self, item):
        """The supplier to buy ``item`` through, or ``None`` if there is none.

        This method USED to be a second, rival answer to that question: it
        filtered orderability and then scored candidates, while every other
        surface in the app resolved the same question by price alone. The
        scoring now lives in :mod:`inventory.services.supplier_selection` and is
        what all of them use (op-2rsp), so this is a thin delegation kept for its
        call site's readability rather than a rule of its own.

        Two behaviour changes came with that move, both deliberate:

        * The scoring raised ``TypeError`` on ``Decimal * float`` for any
          candidate priced below 150% of the item's average, so it never
          completed and this endpoint 500'd on real data. It is Decimal
          throughout now.
        * A flagged primary is a GATE rather than a ``+0.2`` term, so an
          operator's explicit choice can no longer be outbid by a cheaper rival.
        """
        return primary_item_supplier(item)

    def _calculate_optimal_quantity(self, item, supplier):
        """Calculate optimal order quantity considering package sizes and stock needs.

        Always base units. For an item counted in whole packs the item's own
        packaging chain sets the quantity and the supplier's case size is not
        applied on top (op-es7c); ``each`` items keep the supplier round-up.
        """
        # Calculate basic reorder quantity (mode-aware; each = today's math)
        base_quantity = base_reorder_quantity(item)

        if counts_in_packs(item):
            return base_quantity

        # Adjust for package quantities if the vendor declares a case. Asked of
        # the ONE pack-size derivation (op-c1ke) rather than the column, so a
        # recorded 0 is an unknown rather than a silent "sells singles"; the
        # quantity is unchanged for every recorded value.
        declared_case = declares_a_case(supplier)
        if declared_case is not None:
            # Round up to nearest package
            packages_needed = (base_quantity + declared_case - 1) // declared_case
            return packages_needed * declared_case

        return base_quantity

    @action(detail=True, methods=["post"])
    def send_to_supplier(self, request, pk=None):
        """Mark purchase order as sent to supplier."""
        purchase_order = self.get_object()

        if purchase_order.status != PurchaseOrder.Status.DRAFT:
            return Response(
                {"error": "Only draft orders can be sent to suppliers"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        self._mark_sent(purchase_order, request.user)

        serializer = self.get_serializer(purchase_order)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def confirm_order(self, request, pk=None):
        """Mark purchase order as confirmed by supplier.

        ``expected_delivery_date`` is optional: omit it and the date already on
        the order is left untouched, so a bare confirm never wipes what the
        operator set at create or edit time. Send a date to override it, or an
        explicit ``null`` to clear it deliberately.
        """
        purchase_order = self.get_object()

        if purchase_order.status != PurchaseOrder.Status.SENT:
            return Response(
                {"error": "Only sent orders can be confirmed"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # An ABSENT key is not a supplied null. The web app confirms with no
        # request body at all (PurchaseOrderPage.tsx -> confirmOrder(orderId)),
        # so reading the key with a None default and passing it on wiped the
        # expected delivery date the operator had already set — silently, with a
        # 200, taking the due_on_receipt/cod payment due date and receiving's
        # expected date down with it. Three inputs, three answers: an absent key
        # leaves the date alone, an explicit null clears it deliberately, and
        # anything else is validated — a malformed value (including "") is a
        # clean 400 before any write rather than another silent wipe.
        #
        # Same coercion trap as inventory generate_work_order (BACKEND-18): the
        # client's date arrives as a string, and handing it straight to the
        # service persists fine but leaves a str on the in-memory PO, so
        # render_payment_schedule() calls .isoformat() on it and 500s while
        # serializing the response — after the PO has already been confirmed.
        if "expected_delivery_date" in request.data:
            raw_expected = request.data["expected_delivery_date"]
            if raw_expected is None:
                expected_delivery_date = None
            else:
                expected_delivery_date = serializers.DateField().to_internal_value(raw_expected)
            services.confirm_order(purchase_order, expected_delivery_date)
        else:
            services.confirm_order(purchase_order)

        serializer = self.get_serializer(purchase_order)
        return Response(serializer.data)

    @action(detail=True, methods=["get"], url_path="export-order")
    def export_order(self, request, pk=None):
        """Export this PO as a supplier-appropriate order artifact (op-svpq).

        The PO supplier's ``ordering_adapter`` selects what this emits from the
        non-voided lines (each line's ``supplier_sku`` + ``quantity_ordered``):

        - ``amazon`` → one or more Amazon add-to-cart URLs (``cart_urls``); each
          ``supplier_sku`` must be an ASIN.
        - ``hdsupply`` → a ``Part Number,Quantity`` CSV (HD Supply Saved-List
          format) plus a ``part\\tqty`` paste block for their Quick Order pad;
          each ``supplier_sku`` must be numeric.
        - ``generic_csv`` / ``none`` (default) → the vendor-agnostic ``part#,qty``
          order pad (#855), which pastes/uploads into any distributor bulk order
          pad with zero per-vendor integration and zero schema change.

        Lines whose ``supplier_sku`` is blank are surfaced in ``missing_sku`` and
        lines whose SKU fails the adapter's format are surfaced in
        ``invalid_sku`` — never silently dropped, so a mis-entered part number is
        caught before it reaches a vendor cart.

        Unified response: ``{adapter, cart_urls?, csv?, text?, filename?,
        supplier, line_count, missing_sku, invalid_sku}``.

        Read-gated to authenticated users (via :meth:`get_permissions`), matching
        the ``send_to_supplier`` action.
        """
        purchase_order = self.get_object()

        lines = [
            (
                _po_line_display_name(po_item),
                po_item.item_supplier.supplier_sku if po_item.item_supplier else "",
                po_item.quantity_ordered,
            )
            for po_item in purchase_order.items.all()
            if not po_item.is_voided
        ]

        supplier = purchase_order.supplier if purchase_order.supplier_id else None
        adapter = supplier.ordering_adapter if supplier else Supplier.OrderingAdapter.NONE

        if adapter == Supplier.OrderingAdapter.AMAZON:
            payload = build_amazon_cart(lines)
        elif adapter == Supplier.OrderingAdapter.HDSUPPLY:
            payload = build_order_pad(
                lines,
                header=("Part Number", "Quantity"),
                validate=is_valid_hdsupply_part,
            )
        else:
            payload = build_order_pad(lines)

        payload["adapter"] = adapter
        payload["supplier"] = supplier.name if supplier else ""

        # A downloadable filename only makes sense for the CSV adapters; the
        # Amazon adapter emits URLs, not a file.
        if "csv" in payload:
            # PO numbers here already carry a "PO-" prefix; only add one when the
            # number (or the draft fallback) doesn't, so it's never "PO-PO-…".
            po_number = purchase_order.po_number or f"draft-{purchase_order.pk}"
            base = po_number if po_number.upper().startswith("PO-") else f"PO-{po_number}"
            payload["filename"] = f"{base}-order.csv"

        return Response(payload)

    @action(detail=True, methods=["post"], url_path="mark-delivered")
    def mark_delivered(self, request, pk=None):
        """Manually mark a purchase order as delivered on a given date.

        Creates an OrderDelivery covering all pending quantities, updates
        inventory stock, advances the PO status, and records LeadTimeLog
        entries so the Lead Time Analysis report has data even when barcode
        scanning isn't used at receipt.
        """
        from datetime import datetime

        purchase_order = self.get_object()

        if purchase_order.status not in PurchaseOrder.RECEIVABLE_STATUSES:
            return Response(
                {
                    "error": (
                        "Purchase order must be sent, confirmed, or partially "
                        "received to mark as delivered"
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = MarkDeliveredSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # The same "which lines does receiving still owe?" the receipt itself
        # asks — asked once, in one place. A voided line and one closed short
        # are settled, so neither keeps this action alive nor gets stocked by it.
        if not services.outstanding_lines(purchase_order):
            return Response(
                {"error": services.close_out_refusal(purchase_order)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        delivery_datetime = timezone.make_aware(
            datetime.combine(data["delivery_date"], datetime.min.time())
        )

        services.mark_delivered_receipt(
            purchase_order,
            received_by=request.user,
            delivery_datetime=delivery_datetime,
            tracking_number=data.get("tracking_number", ""),
            carrier=data.get("carrier", ""),
            receipt_notes=data.get("receipt_notes", ""),
        )

        record_audit_event(
            action=PurchaseOrderAuditEvent.Action.PO_MARK_DELIVERED,
            actor=request.user,
            purchase_order=purchase_order,
            notes=data.get("receipt_notes", ""),
            metadata={
                "delivery_date": delivery_datetime.isoformat(),
                "tracking_number": data.get("tracking_number", ""),
                "carrier": data.get("carrier", ""),
                "fully_received": purchase_order.status == PurchaseOrder.Status.RECEIVED,
            },
        )

        response_serializer = self.get_serializer(purchase_order)
        return Response(response_serializer.data)

    @action(detail=True, methods=["post"], url_path="receive")
    def receive(self, request, pk=None):
        """Receive specific line items with explicit per-item quantities.

        Where ``mark_delivered`` receives every outstanding quantity on the
        whole PO, this records a receipt of exactly the quantities supplied per
        line — the flow ScanTTY and the web UI use for a delivery that contains
        only some of what was ordered. Shares the receipt side effects
        (delivery, stock, serials, status, lead-time logs) with
        ``mark_delivered`` via :func:`services.receive_delivery`, and the whole
        action is one transaction: a rejected receipt writes nothing at all.

        Per line, beyond ``purchase_order_item`` and ``quantity_received``:

        * ``at_level: true`` reports the quantity as whole packs of the item's
          ``count_level`` — "three cases came in" — converted to base units
          before the figure is recorded or anything is credited (op-ev14).
          Without it the quantity is base units exactly as before. Invalid on a
          line whose item is not counted in packs, and on asset/freeform lines.
        * ``serials`` accessions the units that arrived, each naming the
          identity it belongs to — on a kit line the COMPONENTS the receipt
          credits, never the kit. Captured inside the receipt's transaction, so
          a serial that cannot be saved rolls the receipt back rather than
          leaving stock credited and the operator's serials lost. Fewer serials
          than units is allowed; more is a 400.
        * ``close_short`` (with an optional ``close_short_reason``) declares
          that whatever is still outstanding *after* this receipt is not
          coming, settling the line in the same request as the receipt that
          revealed the shortfall.

        **The quantity is recorded as sent, including more than was ordered.**
        The pending-quantity guard that used to reject an over-receipt with a
        400 is gone: what arrived is credited and the difference stays visible
        on the line as ``quantity_variance`` and ``receipt_state``. A line that
        is voided, or whose balance has been closed short, is still refused —
        nothing more is coming for either.
        """
        from datetime import datetime

        purchase_order = self.get_object()

        if purchase_order.status not in PurchaseOrder.RECEIVABLE_STATUSES:
            return Response(
                {
                    "error": (
                        "Purchase order must be sent, confirmed, or partially "
                        "received to receive items"
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ReceiveItemsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        po_items_by_id = {item.id: item for item in purchase_order.items.all()}
        resolved_lines = []
        closures = []
        for line in data["items"]:
            po_item_id = line["purchase_order_item"]
            quantity = line["quantity_received"]

            po_item = po_items_by_id.get(po_item_id)
            if po_item is None:
                return Response(
                    {"error": f"Line item {po_item_id} does not belong to this purchase order"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            refusal = services.receipt_refusal(po_item)
            if refusal is not None:
                return Response(
                    {"error": f"Line item {po_item_id} {refusal}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # "N packs arrived" → base units, before anything compares it
            # against the base-unit order (op-ev14).
            if line.get("at_level"):
                if po_item.item is None:
                    return Response(
                        {
                            "error": (
                                f"Line item {po_item_id} has no inventory item, so a "
                                "pack count cannot be converted; send base units"
                            )
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                try:
                    quantity = resolve_base_quantity(po_item.item, quantity, at_level=True)
                except DjangoValidationError as exc:
                    return Response(
                        {"error": f"Line item {po_item_id}: {exc.messages[0]}"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

            try:
                serials = _resolve_receive_serials(po_item, line.get("serials") or [])
            except DjangoValidationError as exc:
                return Response(
                    {"error": f"Line item {po_item_id}: {exc.messages[0]}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            resolved_lines.append(
                services.LineReceipt(po_item=po_item, quantity=quantity, serials=serials)
            )
            if line.get("close_short"):
                closures.append((po_item, line.get("close_short_reason", "")))

        delivery_date = data.get("delivery_date")
        if delivery_date is not None:
            delivery_datetime = timezone.make_aware(
                datetime.combine(delivery_date, datetime.min.time())
            )
        else:
            delivery_datetime = timezone.now()

        # ONE transaction over the whole action — the receipt, the closures it
        # settles, and the audit event. A rejected receipt writes nothing at
        # all, which is what the API contract promises: before this, a
        # ``close_short`` that raised after the receipt had committed returned
        # 400 with the stock already credited, the delivery created, the order
        # advanced, and no audit event to show for any of it. Nesting the
        # services' own ``atomic`` blocks inside this one turns them into
        # savepoints, so either everything lands or nothing does.
        try:
            with transaction.atomic():
                delivery = services.receive_delivery(
                    purchase_order,
                    resolved_lines,
                    received_by=request.user,
                    delivery_datetime=delivery_datetime,
                    tracking_number=data.get("tracking_number", ""),
                    carrier=data.get("carrier", ""),
                    receipt_notes=data.get("receipt_notes", ""),
                )
                # Written off in the same request as the receipt that revealed
                # the shortfall, so "8 of 10 arrived and the rest is cancelled"
                # is one operator action rather than two that can half-happen.
                if closures:
                    services.close_lines_short(purchase_order, closures, actor=request.user)
                    services.refresh_delivery_completion(delivery, purchase_order)

                record_audit_event(
                    action=PurchaseOrderAuditEvent.Action.PO_RECEIVE_ITEMS,
                    actor=request.user,
                    purchase_order=purchase_order,
                    notes=data.get("receipt_notes", ""),
                    metadata={
                        "delivery_date": delivery_datetime.isoformat(),
                        "tracking_number": data.get("tracking_number", ""),
                        "carrier": data.get("carrier", ""),
                        "fully_received": (purchase_order.status == PurchaseOrder.Status.RECEIVED),
                        "received_items": [
                            {
                                "purchase_order_item": receipt.po_item.id,
                                "quantity_received": receipt.quantity,
                                # The mismatch the captain chases a vendor with,
                                # on the audit trail as well as on the line.
                                "quantity_variance": receipt.po_item.quantity_variance,
                                "receipt_state": receipt.po_item.receipt_state,
                                "serials": [capture.serial_number for capture in receipt.serials],
                            }
                            for receipt in resolved_lines
                        ],
                    },
                )
        except DjangoValidationError as exc:
            return Response({"error": exc.messages[0]}, status=status.HTTP_400_BAD_REQUEST)

        purchase_order.refresh_from_db()
        response_serializer = self.get_serializer(purchase_order)
        return Response(response_serializer.data)

    @action(detail=True, methods=["get"], url_path="receiving")
    def receiving(self, request, pk=None):
        """Everything a client needs to drive a receipt against this order.

        ``GET .../receiving/`` — the receiving worksheet. It answers, in one
        round trip, the questions a receive screen (web or ScanTTY) has to ask
        before it can show anything:

        * may this order be received against at all, and if not, why not;
        * which lines are still outstanding, and which are already settled —
          received in full, over-received, or closed short;
        * for each line, what identifiers a scanner will see on the box
          (``scan_codes``: the item's own SKU and barcodes plus the vendor's),
          so a scanned code can be matched to a line;
        * for each line, which identities may carry serial numbers and how many
          units of each — the kit's COMPONENTS on a kit line, never the kit.

        Read-only and side-effect-free. Deliberately a *derived view over the
        order*, not a stored worksheet: there is nothing to get out of date, and
        a receipt recorded by another client is reflected the next time this is
        fetched.
        """
        purchase_order = self.get_object()
        return Response(services.build_receiving_worksheet(purchase_order))

    @action(detail=True, methods=["post"], url_path="close-short")
    def close_short(self, request, pk=None):
        """Write off the outstanding balance on named lines as never arriving.

        ``POST .../close-short/`` with
        ``{"items": [{"purchase_order_item": 12, "reason": "backorder cancelled"}]}``.

        This is how a short receipt *ends*. Receiving 8 of 10 leaves the line
        partially received and still expecting 2; closing it short says the 2
        are not coming, which settles the line without ever pretending 10
        arrived. The shortfall stays on the line for good as
        ``quantity_variance`` and ``receipt_state=closed_short``.

        Refuses a line that is already closed short, one that is voided, and one
        that has nothing outstanding — in each case rather than overwriting the
        reason and actor already recorded.

        Advances the order to ``received`` when this settles the last
        outstanding line AND something has actually been received against the
        order. Writing every line off without a single delivery does not make an
        order ``received`` — that status is a claim that goods arrived — so such
        an order stays ``sent`` or ``confirmed`` and is closed out by voiding or
        cancelling the ORDER instead.
        """
        purchase_order = self.get_object()

        if purchase_order.status not in PurchaseOrder.RECEIVABLE_STATUSES:
            return Response(
                {
                    "error": (
                        "Purchase order must be sent, confirmed, or partially "
                        "received to close lines short"
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = CloseShortSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        po_items_by_id = {item.id: item for item in purchase_order.items.all()}
        closures = []
        for line in serializer.validated_data["items"]:
            po_item = po_items_by_id.get(line["purchase_order_item"])
            if po_item is None:
                return Response(
                    {
                        "error": (
                            f"Line item {line['purchase_order_item']} does not belong to "
                            "this purchase order"
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            closures.append((po_item, line.get("reason", "")))

        try:
            closed = services.close_lines_short(purchase_order, closures, actor=request.user)
        except DjangoValidationError as exc:
            return Response({"error": exc.messages[0]}, status=status.HTTP_400_BAD_REQUEST)

        record_audit_event(
            action=PurchaseOrderAuditEvent.Action.PO_RECEIVE_ITEMS,
            actor=request.user,
            purchase_order=purchase_order,
            notes="Closed short",
            metadata={
                "closed_short": [
                    {
                        "purchase_order_item": po_item.id,
                        "quantity_ordered": po_item.quantity_ordered,
                        "quantity_received": po_item.quantity_received,
                        "quantity_variance": po_item.quantity_variance,
                        "reason": po_item.closed_short_reason,
                    }
                    for po_item in closed
                ],
                "fully_received": purchase_order.status == PurchaseOrder.Status.RECEIVED,
            },
        )

        purchase_order.refresh_from_db()
        return Response(self.get_serializer(purchase_order).data)

    @action(detail=True, methods=["post"], url_path="reopen-short")
    def reopen_short(self, request, pk=None):
        """Take back a close-short on named lines — the correction for one made in error.

        ``POST .../reopen-short/`` with
        ``{"items": [{"purchase_order_item": 12, "reason": "closed the wrong line"}]}``.

        A CORRECTION, not an undo. The close-short stays on the line exactly as
        it was recorded — actor, timestamp and reason — and this reopen is
        stamped beside it to the same standard, so the history reads as a
        mistake and its correction and never as a clean slate. The two are
        separate, separately attributable events on the audit trail.

        The line becomes outstanding again and can be received against, and the
        order's status is re-derived in the same transaction: one that had
        already reached ``received`` drops back to ``partially_received``,
        because it is once again waiting on something.

        Allowed on an order that has finished receiving, unlike the other
        receiving actions — a wrongly closed line is most often noticed *after*
        the close settled the order. A draft, cancelled or voided order is
        refused: there is no receiving to correct.

        Refuses a line that is not currently closed short, rather than stamping
        a correction over nothing.
        """
        purchase_order = self.get_object()

        if purchase_order.status not in PurchaseOrder.IN_RECEIVING_STATUSES:
            return Response(
                {
                    "error": (
                        "Purchase order must be sent, confirmed, partially received, "
                        "or received to reopen a line closed short"
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ReopenShortSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        po_items_by_id = {item.id: item for item in purchase_order.items.all()}
        reopenings = []
        for line in serializer.validated_data["items"]:
            po_item = po_items_by_id.get(line["purchase_order_item"])
            if po_item is None:
                return Response(
                    {
                        "error": (
                            f"Line item {line['purchase_order_item']} does not belong to "
                            "this purchase order"
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            reopenings.append((po_item, line.get("reason", "")))

        try:
            reopened = services.reopen_lines_short(purchase_order, reopenings, actor=request.user)
        except DjangoValidationError as exc:
            return Response({"error": exc.messages[0]}, status=status.HTTP_400_BAD_REQUEST)

        record_audit_event(
            action=PurchaseOrderAuditEvent.Action.PO_LINE_REOPEN_SHORT,
            actor=request.user,
            purchase_order=purchase_order,
            notes="Reopened after close-short",
            metadata={
                "reopened_short": [
                    {
                        "purchase_order_item": po_item.id,
                        "quantity_ordered": po_item.quantity_ordered,
                        "quantity_received": po_item.quantity_received,
                        "quantity_pending": po_item.quantity_pending,
                        "reason": po_item.reopened_reason,
                        # The close-short being corrected, carried on the
                        # correction itself so the trail names what it undid
                        # without a join back to the earlier event.
                        "corrects_close_short_at": po_item.closed_short_at.isoformat(),
                        "corrects_close_short_reason": po_item.closed_short_reason,
                    }
                    for po_item in reopened
                ],
                "fully_received": purchase_order.status == PurchaseOrder.Status.RECEIVED,
            },
        )

        purchase_order.refresh_from_db()
        return Response(self.get_serializer(purchase_order).data)

    @action(detail=True, methods=["post"], url_path="mark-received")
    def mark_received(self, request, pk=None):
        """Finish the order off — step 6 of the receiving flow.

        ``POST .../mark-received/`` with an optional ``{"reason": "..."}``.

        Closes every line still outstanding short, recording ``reason`` against
        each. The bulk form of ``close-short``, for the ordinary case where the
        operator has finished unpacking and whatever has not turned up is not
        going to.

        The order advances to ``received`` only if something was actually
        received against it. Written off with nothing ever delivered, it stays
        ``sent`` or ``confirmed`` and the way to finish with it is to void or
        cancel the ORDER; the refusal this action returns once every line is
        settled says so.

        Distinct from ``mark-delivered``, which asserts the opposite — that
        every outstanding quantity *did* arrive and should be received and
        stocked. This one stocks nothing; it writes the shortfall off. Choosing
        between them is the difference between an honest record and a tidy one,
        so neither is a default for the other.

        Refuses an order that has nothing outstanding, rather than silently
        doing nothing.
        """
        purchase_order = self.get_object()

        if purchase_order.status not in PurchaseOrder.RECEIVABLE_STATUSES:
            return Response(
                {
                    "error": (
                        "Purchase order must be sent, confirmed, or partially "
                        "received to be marked received"
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = MarkReceivedSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data.get("reason", "")

        outstanding = services.outstanding_lines(purchase_order)
        if not outstanding:
            return Response(
                {"error": services.close_out_refusal(purchase_order)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            closed = services.close_lines_short(
                purchase_order, [(po_item, reason) for po_item in outstanding], actor=request.user
            )
        except DjangoValidationError as exc:
            return Response({"error": exc.messages[0]}, status=status.HTTP_400_BAD_REQUEST)

        record_audit_event(
            action=PurchaseOrderAuditEvent.Action.PO_RECEIVE_ITEMS,
            actor=request.user,
            purchase_order=purchase_order,
            notes=reason,
            metadata={
                "marked_received": True,
                "closed_short": [
                    {
                        "purchase_order_item": po_item.id,
                        "quantity_variance": po_item.quantity_variance,
                    }
                    for po_item in closed
                ],
            },
        )

        purchase_order.refresh_from_db()
        return Response(self.get_serializer(purchase_order).data)

    @action(detail=True, methods=["get"], url_path="item-lookup")
    def item_lookup(self, request, pk=None):
        """Resolve a typed/scanned identifier against this order's supplier.

        ``GET .../item-lookup/?q=<identifier>`` where the identifier is whatever
        the operator has in front of them — the item's name, the item's own SKU,
        a package or unit barcode, or the vendor's SKU for it (oms-po-add-item).

        Read-only and deliberately non-committal: it never adds anything and it
        never picks between equally-good matches. ``candidates`` comes back
        strongest-match-first with the reason each one matched, ``resolves``
        says whether a client may add straight from it without asking the
        operator, and ``unavailable`` explains items the identifier really does
        name that this order still cannot carry — the supplier does not supply
        them, or supplies them no longer. Non-browser clients (ScanTTY) drive
        the same endpoint the web UI does.
        """
        purchase_order = self.get_object()
        query = request.query_params.get("q", "")
        result = services.lookup_candidates(purchase_order, query)
        return Response(services.serialize_lookup(purchase_order, query.strip(), result))

    @action(detail=True, methods=["post"], url_path="items")
    def add_item(self, request, pk=None):
        """Add a line to a **draft** purchase order (oms-po-add-item).

        ``POST .../items/`` naming what to add exactly one way — ``identifier``
        (typed or scanned), an explicit ``item_supplier`` chosen from a previous
        ambiguous response, an ``asset`` id, or a freeform ``description``.
        Those are the three shapes ``PurchaseOrderItem`` supports and the three
        the create payload accepts, so an order that could be *created* with a
        line can also have that line *added* — the gap that made a forgotten
        asset or one-off charge mean retyping the order.

        On the inventory shapes quantity and unit cost are optional — omitted,
        they are derived from the supplier relationship and this item's purchase
        history so a bare scan still produces a fully-formed line. Asset and
        freeform lines have no such relationship, so ``unit_cost`` is required
        on them, exactly as it is at create time.

        Every guard is enforced here rather than in the UI, because ScanTTY and
        any other API client reach the same code path:

        * the order must be DRAFT (400 ``not_draft``);
        * the order's supplier must actually supply the item — and, for an
          asset, must be its recorded manufacturer
          (400 ``supplier_mismatch`` / ``not_supplied`` / ``discontinued``);
        * an identifier matching several items the supplier carries resolves to
          nothing (409 ``ambiguous``, with the candidate set to choose from).

        Re-adding something already on the order grows the existing line rather
        than creating a second one — by one supplier package when no explicit
        quantity is given — and a ``work_order``/``owning_group`` that clashes
        with one the existing line already carries is refused
        (400 ``work_order_conflict`` / ``owning_group_conflict``) rather than
        silently dropped or silently reassigned. See
        :func:`reorder_queue.services.line_entry.add_line_item`.

        Returns the created/updated line, what matched, and the **full**
        refreshed purchase order so the caller can patch its view in place
        (docs/REACTIVE_MUTATIONS.md).
        """
        purchase_order = self.get_object()

        serializer = AddPurchaseOrderLineSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            services.assert_addable(purchase_order)

            work_order = self._resolve_optional_work_order(data.get("work_order"))
            owning_group = self._resolve_optional_group(data.get("owning_group"))
            shared = {
                "quantity": data.get("quantity"),
                "notes": data.get("notes", ""),
                "work_order": work_order,
                "owning_group": owning_group,
            }

            # The serializer has already proved exactly one shape was named, so
            # this dispatch is a straight three-way and needs no else-error.
            item_supplier = None
            match = None
            if data.get("asset") is not None:
                asset = services.resolve_asset(purchase_order, data["asset"])
                line_item, created = services.add_asset_line_item(
                    purchase_order, asset, unit_cost=data["unit_cost"], **shared
                )
            elif data.get("description") is not None:
                line_item, created = services.add_freeform_line_item(
                    purchase_order, data["description"], unit_cost=data["unit_cost"], **shared
                )
            else:
                if data.get("item_supplier") is not None:
                    item_supplier = services.resolve_item_supplier(
                        purchase_order, data["item_supplier"]
                    )
                else:
                    candidate = services.resolve_identifier(purchase_order, data["identifier"])
                    item_supplier = candidate.item_supplier
                    match = services.serialize_candidate(candidate)

                line_item, created = services.add_line_item(
                    purchase_order,
                    item_supplier,
                    unit_cost=data.get("unit_cost"),
                    **shared,
                )
        except services.LineEntryError as exc:
            payload = {"error": exc.message, "code": exc.code}
            if exc.code == "ambiguous":
                payload["candidates"] = exc.candidates
                return Response(payload, status=status.HTTP_409_CONFLICT)
            return Response(payload, status=status.HTTP_400_BAD_REQUEST)

        record_audit_event(
            action=PurchaseOrderAuditEvent.Action.PO_LINE_ADD,
            actor=request.user,
            line_item=line_item,
            notes=data.get("notes", ""),
            metadata={
                # ``line_shape`` names which of the three targets was written,
                # so an audit reader never has to infer it from which of the
                # target keys happens to be present.
                "line_shape": line_item.target_type,
                "item_supplier": item_supplier.pk if item_supplier else None,
                "item_id": str(item_supplier.item_id) if item_supplier else None,
                "item_name": item_supplier.item.name if item_supplier else None,
                "supplier_sku": item_supplier.supplier_sku if item_supplier else None,
                "asset_id": str(line_item.asset_id) if line_item.asset_id else None,
                "description": line_item.description or "",
                "quantity_ordered": line_item.quantity_ordered,
                "unit_cost_ordered": str(line_item.unit_cost_ordered),
                "created": created,
                "identifier": data.get("identifier", ""),
                "match_kind": (match or {}).get("match_kind"),
            },
        )

        # A grow that overrode a different price both added quantity AND
        # repriced the line, so it gets both rows — see ``record_line_reprice``.
        repriced_from = getattr(line_item, "repriced_from", None)
        if repriced_from is not None:
            record_line_reprice(
                line_item=line_item,
                previous_unit_cost=repriced_from,
                actor=request.user,
            )

        # The viewset prefetches ``items``, so the instance in hand still holds
        # the pre-add line set; re-read it so the returned order includes the
        # new line and the re-rolled estimated total.
        refreshed = self.get_queryset().get(pk=purchase_order.pk)

        from .serializers import PurchaseOrderItemSerializer

        return Response(
            {
                "created": created,
                "line_item": PurchaseOrderItemSerializer(
                    line_item, context=self.get_serializer_context()
                ).data,
                "match": match,
                "purchase_order": PurchaseOrderSerializer(
                    refreshed, context=self.get_serializer_context()
                ).data,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def _resolve_optional_work_order(self, work_order_id):
        """Resolve an optional line-level work order, mirroring ``update_item``."""
        if work_order_id in (None, ""):
            return None
        from inventory.models import WorkOrder

        try:
            return WorkOrder.objects.get(id=work_order_id)
        except (WorkOrder.DoesNotExist, DjangoValidationError, ValueError, TypeError):
            raise services.LineEntryError(
                f"Work order {work_order_id} not found.", "work_order_not_found"
            )

    def _resolve_optional_group(self, group_id):
        """Resolve an optional line-level committee, mirroring ``update_item``."""
        if group_id in (None, ""):
            return None
        from django.contrib.auth.models import Group

        try:
            return Group.objects.get(id=group_id)
        except (Group.DoesNotExist, DjangoValidationError, ValueError, TypeError):
            raise services.LineEntryError(f"Committee {group_id} not found.", "group_not_found")

    @action(detail=True, methods=["patch", "delete"], url_path="items/(?P<item_id>[^/.]+)")
    def update_item(self, request, pk=None, item_id=None):
        """Update — or, on a pre-send order, DESTROY — a specific line item.

        ``DELETE`` is a pure ADDITION to this path: the method previously
        answered 405 here, and ``PATCH`` below is untouched. It shares the
        action (and therefore the URL name ``purchaseorder-update-item``)
        because DRF routes one url_path to one view function — a second
        ``@action`` on the same path would be shadowed by this one and answer
        405 for ever, which is a worse thing to discover in production than a
        two-line dispatch is to read here.

        See :meth:`_destroy_item` for what deletion means and why it is refused
        once the supplier holds a copy.
        """
        purchase_order = self.get_object()
        try:
            line_item = PurchaseOrderItem.objects.get(id=item_id, purchase_order=purchase_order)
        except PurchaseOrderItem.DoesNotExist:
            return Response({"error": "Line item not found"}, status=status.HTTP_404_NOT_FOUND)

        if request.method == "DELETE":
            return self._destroy_item(request, purchase_order, line_item)

        # Quantity edits (op-yh4h) — "we actually need 12, not 10" on an order
        # that is already out. Applied before the cost branches below so a
        # combined {quantity_ordered, line_cost} PATCH divides the line cost by
        # the NEW quantity.
        quantity_changed = False
        if "quantity_ordered" in request.data:
            raw_quantity = request.data["quantity_ordered"]
            try:
                new_quantity = int(str(raw_quantity))
            except (TypeError, ValueError):
                return Response(
                    {"error": f"Invalid quantity ordered value: {raw_quantity!r}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if new_quantity < 1:
                return Response(
                    {"error": "Quantity ordered must be a positive integer"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # "make it 3 cases" — convert to base units before the guards below,
            # which all compare against base-unit columns (op-ev14). Absent
            # ``at_level`` the quantity is base units, as it always was.
            try:
                at_level = parse_at_level(request.data.get("at_level"))
                if at_level and line_item.item_supplier_id is not None:
                    new_quantity = resolve_base_quantity(
                        line_item.item_supplier.item, new_quantity, at_level=True
                    )
            except DjangoValidationError as exc:
                return Response(
                    {"error": exc.messages[0]},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if line_item.is_voided:
                return Response(
                    {"error": "Cannot change the quantity of a voided line item"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Same window the receive flow treats as "in flight", plus draft.
            # A closed order (received/cancelled/voided) is settled — reopening
            # it by re-ordering more is a new PO, not a line edit.
            if purchase_order.status not in [
                PurchaseOrder.Status.DRAFT,
                PurchaseOrder.Status.SENT,
                PurchaseOrder.Status.CONFIRMED,
                PurchaseOrder.Status.PARTIALLY_RECEIVED,
            ]:
                return Response(
                    {
                        "error": (
                            "Quantity can only be changed while the purchase order is "
                            "draft, sent, confirmed, or partially received"
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if new_quantity < line_item.quantity_received:
                return Response(
                    {
                        "error": (
                            f"Quantity ordered ({new_quantity}) cannot be less than the "
                            f"quantity already received ({line_item.quantity_received})"
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if new_quantity != line_item.quantity_ordered:
                services.apply_line_quantity(line_item, new_quantity)
                quantity_changed = True

        # Deliberate reprice of a line's ORDERED price (the figure the shop is
        # committing to spend). Draft only, and deliberately stricter than the
        # quantity edit directly above: quantity on a live order is "we need 12,
        # not 10", a thing the supplier can still be told, whereas the ordered
        # price is what the supplier was quoted — once the order has gone out,
        # that is a matter of record, the same boundary ``assert_addable``
        # draws for adding a line at all.
        repriced_from = None
        if "unit_cost_ordered" in request.data:
            # Validated by the same field the add path validates it with, so
            # the two accept-points for this figure cannot disagree about what
            # a valid ordered price is.
            price = RepricePurchaseOrderLineSerializer(
                data={"unit_cost_ordered": request.data["unit_cost_ordered"]}
            )
            if not price.is_valid():
                detail = " ".join(str(msg) for msg in price.errors["unit_cost_ordered"])
                return Response(
                    {"error": f"Invalid unit cost ordered value: {detail}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            new_unit_cost = price.validated_data["unit_cost_ordered"]

            # Refuse a price CHANGE, not the mere presence of the key. A client
            # that GETs a line, edits its notes and PATCHes the whole object
            # back echoes the price it was given, and rejecting that would fail
            # an ordinary round trip — taking the notes edit down with it — over
            # a figure nobody asked to move. An unchanged price is already
            # treated as not-a-reprice below (it records nothing); the guards
            # have to reach the same conclusion.
            current_unit_cost = line_item.unit_cost_ordered
            if current_unit_cost != new_unit_cost:
                if line_item.is_voided:
                    return Response(
                        {"error": "Cannot change the price of a voided line item"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                if purchase_order.status != PurchaseOrder.Status.DRAFT:
                    label = PurchaseOrder.Status(purchase_order.status).label
                    return Response(
                        {
                            "error": (
                                "The ordered price can only be changed while a purchase order "
                                f"is a draft. {purchase_order.po_number or 'This order'} is "
                                f"{label}."
                            )
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                repriced_from = current_unit_cost
                line_item.unit_cost_ordered = new_unit_cost

        # Allow updating expected_shipment_date and notes
        expected_shipment_date = request.data.get("expected_shipment_date")
        if expected_shipment_date is not None:
            if expected_shipment_date == "":
                line_item.expected_shipment_date = None
            else:
                from django.utils.dateparse import parse_date

                parsed_date = parse_date(expected_shipment_date)
                if parsed_date:
                    line_item.expected_shipment_date = parsed_date
                else:
                    return Response(
                        {"error": "Invalid date format. Use YYYY-MM-DD"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

        # Mark-shipped path: setting actual_shipment_date is how scantty
        # (and the web UI) record that the supplier confirmed shipment.
        # Empty string clears it (lets an operator un-mark a typo).
        actual_shipment_date = request.data.get("actual_shipment_date")
        if actual_shipment_date is not None:
            if actual_shipment_date == "":
                line_item.actual_shipment_date = None
            else:
                from django.utils.dateparse import parse_date

                parsed_date = parse_date(actual_shipment_date)
                if parsed_date:
                    line_item.actual_shipment_date = parsed_date
                else:
                    return Response(
                        {"error": "Invalid actual_shipment_date format. Use YYYY-MM-DD"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

        if "notes" in request.data:
            line_item.notes = request.data["notes"]

        # "Ordered for this work order" (op-bu80). Settable after the fact —
        # the job the parts were for is often identified after the order goes
        # out. Empty string / null clears it. Receiving is what actually posts
        # the material onto the work order.
        if "work_order" in request.data:
            work_order_id = request.data["work_order"]
            if work_order_id in (None, ""):
                line_item.work_order = None
            else:
                from inventory.models import WorkOrder

                try:
                    line_item.work_order = WorkOrder.objects.get(id=work_order_id)
                except (WorkOrder.DoesNotExist, DjangoValidationError, ValueError, TypeError):
                    return Response(
                        {"error": f"Work order {work_order_id} not found"},
                        status=status.HTTP_404_NOT_FOUND,
                    )

        # "Ordered on behalf of this committee" (op-shb9), the line-level twin
        # of the work-order tag above. Also settable after the fact; empty
        # string / null clears it. Attribution only — the receiving ledger books
        # the committee from the received item's own owning_group, not this.
        if "owning_group" in request.data:
            owning_group_id = request.data["owning_group"]
            if owning_group_id in (None, ""):
                line_item.owning_group = None
            else:
                from django.contrib.auth.models import Group

                try:
                    line_item.owning_group = Group.objects.get(id=owning_group_id)
                except (Group.DoesNotExist, DjangoValidationError, ValueError, TypeError):
                    return Response(
                        {"error": f"Committee {owning_group_id} not found"},
                        status=status.HTTP_404_NOT_FOUND,
                    )

        # Allow updating unit_cost_actual via line_cost (total cost)
        # If line_cost is provided, calculate unit_cost_actual = line_cost / quantity
        line_cost = request.data.get("line_cost")
        if line_cost is not None:
            from decimal import Decimal, InvalidOperation

            try:
                line_cost_decimal = Decimal(str(line_cost))
                if line_cost_decimal < 0:
                    return Response(
                        {"error": "Line cost cannot be negative"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                # Use quantity_ordered for calculation (what was ordered)
                # This allows users to input the total line cost for the order
                quantity = line_item.quantity_ordered
                if quantity <= 0:
                    return Response(
                        {
                            "error": "Cannot calculate unit cost: quantity ordered must be greater than 0"
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                # Calculate unit cost from line cost: unit_cost = line_cost / quantity_ordered
                unit_cost_actual = line_cost_decimal / Decimal(quantity)
                line_item.unit_cost_actual = unit_cost_actual
            except (InvalidOperation, ValueError, TypeError) as e:
                return Response(
                    {"error": f"Invalid line cost value: {str(e)}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        # Also allow direct unit_cost_actual update for backwards compatibility
        elif "unit_cost_actual" in request.data:
            from decimal import Decimal, InvalidOperation

            try:
                unit_cost_actual = Decimal(str(request.data["unit_cost_actual"]))
                if unit_cost_actual < 0:
                    return Response(
                        {"error": "Unit cost cannot be negative"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                line_item.unit_cost_actual = unit_cost_actual
            except (InvalidOperation, ValueError, TypeError) as e:
                return Response(
                    {"error": f"Invalid unit cost value: {str(e)}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        with transaction.atomic():
            line_item.save()
            # The PO-level estimated_total is frozen at create time from each
            # line's estimated_cost, so a quantity or price edit has to re-roll it.
            if quantity_changed or repriced_from is not None:
                services.recalculate_estimated_total(purchase_order)
            if quantity_changed:
                # A quantity edit is a SETTLEMENT transition, not only a cost
                # one: lowering a line to what has already arrived leaves it
                # received in full, and if it was the last outstanding line the
                # order has just finished receiving. Without this the order sat
                # at ``partially_received`` with nothing outstanding, which both
                # close-out actions refuse — a state reachable and not leavable.
                # Same re-derivation ``void_line_item`` and the close-short
                # actions use, so the four routes cannot disagree.
                #
                # Re-read rather than reusing ``purchase_order``: the viewset
                # prefetches ``items``, so that instance's cached relation still
                # holds the pre-edit quantities (the same trap
                # ``recalculate_estimated_total`` documents).
                services.refresh_receipt_status(PurchaseOrder.objects.get(pk=purchase_order.pk))

        # Only when the price actually moved. A price change that leaves no
        # trace is the very thing the add path's ``price_conflict`` refusal
        # exists to prevent, so the deliberate route has to record both the
        # figure it replaced and the one it wrote.
        if repriced_from is not None:
            record_line_reprice(
                line_item=line_item,
                previous_unit_cost=repriced_from,
                actor=request.user,
            )

        from .serializers import PurchaseOrderItemSerializer

        serializer = PurchaseOrderItemSerializer(line_item, context=self.get_serializer_context())
        return Response(serializer.data)

    def _destroy_item(self, request, purchase_order, line_item):
        """Destroy a line on an order the supplier has not seen (oms-po-line-delete).

        The counterpart to :meth:`void_item`, and deliberately NOT a variant of
        it. While the order is the shop's own document a line added by mistake
        is a typo, and the honest record of a typo is no line at all — leaving a
        struck-off ghost with a mandatory written reason misrepresents what
        happened. Once the supplier holds a copy the line is part of a record
        someone else also has, so it can only be voided, and this refuses with
        that alternative named (``assert_deletable``).

        **Nothing is stranded, and the set was derived rather than guessed** —
        from ``PurchaseOrderItem``'s own reverse relations, not from a search of
        this app:

        * ``DeliveryItem`` (CASCADE) — a receipt line. Unreachable here; see
          the receipts note below.
        * ``inventory.WorkOrderMaterialUsage`` (SET_NULL) — written by the
          receiving bridge, so likewise only exists once goods have arrived.
        * ``inventory.SerializedComponent.provenance_purchase_order_item``
          (SET_NULL) — provenance stamped at receipt, same.
        * ``PurchaseOrderAuditEvent.line_item`` (SET_NULL) — the trail of what
          was done to this line, and the one reference that DOES exist on a
          draft. SET_NULL is what keeps it: the rows survive with their
          ``purchase_order`` FK and metadata intact, which is why the event
          recorded below carries the whole line in its metadata rather than
          relying on an FK that is about to be nulled.

        A draft line's ``item_supplier``, ``asset``, ``work_order`` and
        ``owning_group`` are things the line points AT, not things that point at
        it; they are unaffected, which is correct — deleting a mistyped line
        must not discontinue a supplier's catalogue entry the way voiding
        deliberately does.

        **A line carrying a receipt is REFUSED, not assumed away.** The
        argument that it cannot happen is a good one — ``DRAFT`` is an
        initial-only state, and every writer of ``quantity_received`` gates on
        ``RECEIVABLE_STATUSES``, which excludes it — but an argument is only
        true as long as every future change re-checks it, and a guard is true
        without anyone re-checking anything. Destroying goods that a receipt
        says arrived is not a failure worth trading for one unreachable branch,
        so the branch is here, and the refusal says what the operator can do
        about it: a receipt on a pre-send order means a quantity was entered
        outside the ordinary receiving flow, and correcting that comes before
        removing the line.

        Neither the order's settlement status nor its stored ``estimated_total``
        is refreshed from here. Both ride the line's own ``post_delete``
        (:mod:`reorder_queue.settlement_signals`), which is what makes this
        endpoint and the Django admin's three delete doors agree without either
        knowing about the other.

        Returns the **full** refreshed purchase order so the caller can patch
        its view in place (docs/REACTIVE_MUTATIONS.md).
        """
        try:
            services.assert_deletable(purchase_order)
        except services.LineEntryError as exc:
            return Response(
                {"error": exc.message, "code": exc.code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # After the pre-send guard on purpose: an order the supplier already
        # holds gets told to void, which is the answer to the question it was
        # actually asked.
        if line_item.quantity_received > 0:
            return Response(
                {
                    "error": (
                        f"This line records {line_item.quantity_received} received, so it "
                        f"cannot be deleted. A receipt on an order the supplier has not "
                        f"been sent means the quantity was entered outside the ordinary "
                        f"receiving flow — correct the received quantity to 0 first, then "
                        f"delete the line."
                    ),
                    "code": "line_received",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Recorded BEFORE the delete, and describing the line in full. The FK is
        # SET_NULL, so a moment from now this row's ``line_item`` is null and
        # this metadata is the only remaining account of what was destroyed.
        destroyed = {
            "line_item": str(line_item.pk),
            "line_shape": line_item.target_type,
            "label": _po_line_display_name(line_item),
            "item_supplier": line_item.item_supplier_id,
            "asset_id": str(line_item.asset_id) if line_item.asset_id else None,
            "description": line_item.description or "",
            "quantity_ordered": line_item.quantity_ordered,
            "unit_cost_ordered": str(line_item.unit_cost_ordered),
            "estimated_cost": str(line_item.estimated_cost),
            "work_order": str(line_item.work_order_id) if line_item.work_order_id else None,
            "owning_group": line_item.owning_group_id,
        }
        # One transaction over both. The FK ordering forces the event to be
        # written first, so without this a delete that raised would leave a
        # ``po_line_delete`` row describing a line that still exists — a
        # phantom in an append-only trail whose whole job here is to be the
        # last account of something that is gone.
        with transaction.atomic():
            record_audit_event(
                action=PurchaseOrderAuditEvent.Action.PO_LINE_DELETE,
                actor=request.user,
                purchase_order=purchase_order,
                line_item=line_item,
                metadata=destroyed,
            )

            services.delete_line_item(line_item)

        # Re-read rather than reusing ``purchase_order``: the viewset prefetches
        # ``items``, so that instance still holds the deleted line and the
        # pre-delete total.
        refreshed = self.get_queryset().get(pk=purchase_order.pk)

        return Response(
            {
                "deleted": destroyed,
                "purchase_order": PurchaseOrderSerializer(
                    refreshed, context=self.get_serializer_context()
                ).data,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="items/(?P<item_id>[^/.]+)/void")
    def void_item(self, request, pk=None, item_id=None):
        """Void a specific line item in a purchase order (e.g., item discontinued)."""
        purchase_order = self.get_object()
        try:
            line_item = PurchaseOrderItem.objects.get(id=item_id, purchase_order=purchase_order)
        except PurchaseOrderItem.DoesNotExist:
            return Response({"error": "Line item not found"}, status=status.HTTP_404_NOT_FOUND)

        if line_item.is_voided:
            return Response(
                {"error": "Line item is already voided"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check if item has been received
        if line_item.quantity_received > 0:
            return Response(
                {
                    "error": "Cannot void line item that has already been received. "
                    "Use notes to document the issue instead."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Void the line item (marks the linked item_supplier discontinued too)
        reason = request.data.get("reason", "Item discontinued by supplier")
        services.void_line_item(line_item, request.user, reason)

        record_audit_event(
            action=PurchaseOrderAuditEvent.Action.PO_LINE_VOID,
            actor=request.user,
            line_item=line_item,
            notes=line_item.void_reason or "",
        )

        from .serializers import PurchaseOrderItemSerializer

        serializer = PurchaseOrderItemSerializer(line_item, context=self.get_serializer_context())
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        """Void an entire purchase order (e.g., orphaned PO with all line items rejected).

        Only staff/superusers or members of the COO group may void POs.
        Cascades to non-voided line items so they stop appearing in queues.
        """
        from django.contrib.auth.models import Group

        user = request.user
        is_coo = False
        try:
            is_coo = Group.objects.get(name="COO") in user.groups.all()
        except Group.DoesNotExist:
            is_coo = False

        if not (user.is_staff or user.is_superuser or is_coo):
            return Response(
                {"detail": "Only staff or COO group members may void purchase orders."},
                status=status.HTTP_403_FORBIDDEN,
            )

        purchase_order = self.get_object()

        if purchase_order.status == PurchaseOrder.Status.VOIDED:
            return Response(
                {"detail": "Purchase order is already voided."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if purchase_order.status == PurchaseOrder.Status.RECEIVED:
            return Response(
                {"detail": "Cannot void a received PO; create a return instead."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reason = request.data.get("reason", "")

        services.void_po(purchase_order, user, reason)

        record_audit_event(
            action=PurchaseOrderAuditEvent.Action.PO_VOID,
            actor=user,
            purchase_order=purchase_order,
            notes=reason,
            metadata={"po_number": purchase_order.po_number},
        )

        serializer = self.get_serializer(purchase_order)
        return Response(serializer.data)

    @action(
        detail=True,
        methods=["post"],
        url_path="upload-attachment",
        parser_classes=[MultiPartParser, FormParser],
    )
    def upload_attachment(self, request, pk=None):
        """Attach a file (sales order, supplier confirmation, etc.) to this PO.

        Any authenticated user may attach files; deletion is restricted to staff
        (see destroy_attachment).
        """
        purchase_order = self.get_object()

        serializer = PurchaseOrderAttachmentSerializer(
            data=request.data, context={"request": request}
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        attachment = serializer.save(purchase_order=purchase_order, uploaded_by=request.user)
        record_audit_event(
            action=PurchaseOrderAuditEvent.Action.ATTACHMENT_ADD,
            actor=request.user,
            purchase_order=purchase_order,
            attachment=attachment,
            metadata={"filename": getattr(attachment.file, "name", "") or ""},
        )
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=["delete"],
        url_path="attachments/(?P<attachment_id>[^/.]+)",
    )
    def destroy_attachment(self, request, pk=None, attachment_id=None):
        """Delete an attachment from this PO. Staff/superuser only."""
        user = request.user
        if not (user.is_staff or user.is_superuser):
            return Response(
                {"detail": "Only staff may delete purchase order attachments."},
                status=status.HTTP_403_FORBIDDEN,
            )

        purchase_order = self.get_object()
        try:
            attachment = purchase_order.attachments.get(pk=attachment_id)
        except PurchaseOrderAttachment.DoesNotExist:
            return Response(
                {"detail": "Attachment not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Record audit BEFORE delete: SET_NULL on the FK means the audit row
        # survives, but we want the filename in metadata while we still have
        # the field on hand.
        record_audit_event(
            action=PurchaseOrderAuditEvent.Action.ATTACHMENT_REMOVE,
            actor=user,
            purchase_order=purchase_order,
            attachment=attachment,
            metadata={"filename": getattr(attachment.file, "name", "") or ""},
        )
        attachment.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["get"])
    def dashboard_summary(self, request):
        """Get summary data for the orders dashboard."""
        # Order status counts
        status_counts = PurchaseOrder.objects.aggregate(
            total=Count("id"),
            draft=Count("id", filter=Q(status=PurchaseOrder.Status.DRAFT)),
            sent=Count("id", filter=Q(status=PurchaseOrder.Status.SENT)),
            confirmed=Count("id", filter=Q(status=PurchaseOrder.Status.CONFIRMED)),
            partially_received=Count(
                "id", filter=Q(status=PurchaseOrder.Status.PARTIALLY_RECEIVED)
            ),
            received=Count("id", filter=Q(status=PurchaseOrder.Status.RECEIVED)),
        )

        # Financial metrics
        financial_metrics = PurchaseOrder.objects.aggregate(
            total_value=Sum("estimated_total"),
            received_value=Sum("actual_total", filter=Q(status=PurchaseOrder.Status.RECEIVED)),
        )

        # Recent activity (this week)
        week_ago = timezone.now() - timedelta(days=7)
        recent_activity = PurchaseOrder.objects.filter(order_date__gte=week_ago).aggregate(
            orders_created=Count("id"),
            orders_received=Count("id", filter=Q(status=PurchaseOrder.Status.RECEIVED)),
        )

        # Items metrics. Two gross running totals — everything ever ordered,
        # everything ever taken in — reported side by side and deliberately NOT
        # subtracted from one another. Their difference is not what is still on
        # its way: a line struck off, or one whose balance was written off as
        # never arriving, leaves a permanent gap between them, and reporting
        # that gap as "pending receipt" claims goods are coming that nobody is
        # waiting for.
        item_totals = PurchaseOrderItem.objects.aggregate(
            total_items_ordered=Sum("quantity_ordered"),
            total_items_received=Sum("quantity_received"),
        )
        total_items_ordered = item_totals["total_items_ordered"]
        total_items_received = item_totals["total_items_received"]
        # What receiving is actually still owed, off the line's own settlement
        # derivation rather than off a subtraction.
        items_pending = PurchaseOrderItem.objects.outstanding().aggregate(
            total=Sum(PurchaseOrderItem.outstanding_quantity_expression())
        )["total"]

        # Calculate pending values
        pending_value = (financial_metrics["total_value"] or 0) - (
            financial_metrics["received_value"] or 0
        )

        # Lead time metrics
        lead_time_data = LeadTimeLog.objects.aggregate(
            avg_lead_time=Avg("actual_lead_time_days"),
            on_time_count=Count("id", filter=Q(variance_days__lte=0)),
            total_deliveries=Count("id"),
        )

        on_time_rate = 0
        if lead_time_data["total_deliveries"] > 0:
            on_time_rate = (
                lead_time_data["on_time_count"] / lead_time_data["total_deliveries"]
            ) * 100

        metrics = OrderMetricsSerializer(
            {
                # Order counts
                "total_orders": status_counts["total"],
                "draft_orders": status_counts["draft"],
                "sent_orders": status_counts["sent"],
                "confirmed_orders": status_counts["confirmed"],
                "partially_received_orders": status_counts["partially_received"],
                "completed_orders": status_counts["received"],
                # Item metrics
                "total_items_on_order": total_items_ordered or 0,
                "total_items_received": total_items_received or 0,
                "items_pending_receipt": items_pending or 0,
                # Financial metrics
                "total_order_value": financial_metrics["total_value"] or 0,
                "received_order_value": financial_metrics["received_value"] or 0,
                "pending_order_value": pending_value,
                # Recent activity
                "orders_created_this_week": recent_activity["orders_created"],
                "orders_received_this_week": recent_activity["orders_received"],
                # Lead time metrics
                "average_lead_time_days": lead_time_data["avg_lead_time"] or 0,
                "on_time_delivery_rate": on_time_rate,
            }
        )

        return Response(metrics.data)


class OrderReceiptViewSet(viewsets.ModelViewSet):
    """API endpoint for order receipt and barcode scanning."""

    queryset = OrderDelivery.objects.select_related(
        "purchase_order__supplier",
        # op-yoos: the nested purchase_order_details carries
        # supplier_agreement_details — joined so it costs no query per delivery.
        "purchase_order__supplier_agreement",
        # op-shb9: same for the order-level work-order / committee associations.
        "purchase_order__work_order",
        "purchase_order__work_order__maintenance_item",
        "purchase_order__work_order__asset",
        "purchase_order__owning_group",
        "received_by",
    ).prefetch_related(
        "items__purchase_order_item__item_supplier__item",
        "purchase_order__work_order__asset_problems",
        "purchase_order__items__owning_group",
    )

    serializer_class = OrderDeliverySerializer
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["post"])
    def scan_barcode(self, request):
        """Process barcode scan for order receipt.

        NOTE (#883): this is a SEPARATE receive path from
        :func:`services.receive_delivery` (used by ``receive``/``mark-delivered``)
        and its side effects are still written inline. Its behaviour diverges in
        ways that do not parameterize cleanly: it upserts one delivery per day
        (``get_or_create`` on ``delivery_date``) instead of always creating a new
        delivery, it records scan-specific ``DeliveryItem`` fields
        (``scanned_upc``/``scanned_at``/``scanned_by``/damage/expiry), it never
        sets ``delivery.is_complete``, and it records NO audit event. Those four
        divergences are still the case; the rest is flagged as a future
        de-duplication.

        What it does NOT decide for itself is settlement. Which lines may still
        take a receipt (:func:`services.receipt_refusal`), what the order's
        status should be afterwards (:func:`services.refresh_receipt_status`),
        and whether this receipt is the delivery that finished a line off
        (:func:`services.receipt_completed_line`) are answered by the same
        functions the shared path uses, because a second opinion on any of them
        produced orders that could never be closed out, receipts that quietly
        erased a written-off shortfall, and one delivery counted twice.

        **More than the outstanding quantity is accepted**, as it is on
        ``receive``: what arrived is credited, and the difference comes back as
        ``quantity_variance``/``receipt_state`` — which ``quantity_remaining``
        cannot carry, being floored at zero. The pending-quantity guard that
        used to answer a scanned over-receipt with a 400 is gone; a line
        ``receipt_refusal`` refuses is still refused, and so is a kit line.
        """
        serializer = BarcodeReceiptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        purchase_order_id = data["purchase_order_id"]
        scanned_upc = data["scanned_upc"]
        quantity_received = data["quantity_received"]

        try:
            purchase_order = PurchaseOrder.objects.get(id=purchase_order_id)
        except PurchaseOrder.DoesNotExist:
            return Response({"error": "Purchase order not found"}, status=status.HTTP_404_NOT_FOUND)

        # Find matching item by UPC
        matching_items = []
        for po_item in purchase_order.items.all():
            item_supplier = po_item.item_supplier
            if item_supplier.package_upc == scanned_upc or item_supplier.unit_upc == scanned_upc:
                matching_items.append(po_item)

        if not matching_items:
            return Response(
                {
                    "error": "No items in this order match the scanned UPC",
                    "scanned_upc": scanned_upc,
                    "order_items": [
                        {
                            "item_name": poi.item.name,
                            "package_upc": poi.item_supplier.package_upc,
                            "unit_upc": poi.item_supplier.unit_upc,
                        }
                        for poi in purchase_order.items.all()
                    ],
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if len(matching_items) > 1:
            return Response(
                {
                    "error": "Multiple items match this UPC",
                    "matching_items": [
                        {"item_name": poi.item.name, "id": poi.id} for poi in matching_items
                    ],
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        po_item = matching_items[0]

        # The same answer the ``receive`` action gets to "may this line still
        # take a receipt?": a struck-off line, or one whose balance has been
        # written off, is finished with. Crediting stock against either would
        # erase the record that says nothing more is coming.
        refusal = services.receipt_refusal(po_item)
        if refusal is not None:
            return Response(
                {"error": f"Line item {po_item.id} {refusal}", "po_item_id": po_item.id},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Kit lines are refused here rather than mis-received (op-8n0). This
        # endpoint DUPLICATES the stock mutation inline instead of routing
        # through ``receiving.receive_delivery``, so it has none of the kit
        # explosion logic: scanning a kit's barcode would credit the KIT's own
        # stock — a number nothing ever draws down — and silently leave all five
        # cartridges unreceived. Failing loud is the only safe option until that
        # duplication is consolidated (tracked separately); a 400 leaves the
        # operator able to receive the line from the purchase-order page.
        if po_item.is_kit_line:
            return Response(
                {
                    "error": (
                        "Kit lines cannot be received by barcode yet. Receive this "
                        "line from the purchase order so its components are credited."
                    ),
                    "po_item_id": po_item.id,
                    "is_kit_line": True,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Create or get delivery for today
        with transaction.atomic():
            delivery, created = OrderDelivery.objects.get_or_create(
                purchase_order=purchase_order,
                delivery_date__date=timezone.now().date(),
                defaults={"received_by": request.user, "delivery_date": timezone.now()},
            )

            # Create delivery item
            DeliveryItem.objects.create(
                delivery=delivery,
                purchase_order_item=po_item,
                quantity_received=quantity_received,
                is_damaged=data.get("is_damaged", False),
                is_expired=data.get("is_expired", False),
                condition_notes=data.get("condition_notes", ""),
                scanned_upc=scanned_upc,
                scanned_at=timezone.now(),
                scanned_by=request.user,
            )

            # Update purchase order item received quantity
            po_item.quantity_received += quantity_received
            po_item.save()

            # Update inventory stock
            item = po_item.item
            item.current_stock += quantity_received
            item.save()

            services.refresh_receipt_status(purchase_order)

            # Whether THIS receipt is the delivery that finished the line off —
            # the question a lead-time log answers, and one more the two paths
            # share rather than each deciding: ``services.receipt_completed_line``.
            if services.receipt_completed_line(po_item, quantity_received):
                self._create_lead_time_log(po_item, delivery.delivery_date)
            if po_item.is_fully_received:
                # Same auto-close as the ``receive``/``mark-delivered`` paths:
                # scanning a delivery in must retire the reorder request too.
                # Shared from the receiving service (like the lead-time log
                # above) because this path is inline rather than routed through
                # ``services.receive_delivery`` — see the note on this action.
                services.close_linked_reorder_request(po_item, delivery.delivery_date)

        return Response(
            {
                "success": True,
                "message": f"Successfully received {quantity_received} units of {po_item.item.name}",
                "item_name": po_item.item.name,
                "quantity_received": quantity_received,
                "total_received": po_item.quantity_received,
                "quantity_remaining": po_item.quantity_pending,
                # ``quantity_remaining`` is floored at zero, so on its own it
                # cannot say a receipt went OVER the order. These three are the
                # same three the worksheet and the line serializer report, so a
                # scanned over-receipt is described in the words every other
                # reader already uses.
                "quantity_variance": po_item.quantity_variance,
                "receipt_state": po_item.receipt_state,
                "receipt_state_label": po_item.receipt_state_label,
                "order_status": purchase_order.status,
                "updated_inventory_stock": item.current_stock,
            }
        )

    def _create_lead_time_log(self, po_item, delivery_date):
        """Create a lead time log entry when an item is fully received."""
        services.create_lead_time_log(po_item, delivery_date)

    @action(detail=False, methods=["get"])
    def pending_orders(self, request):
        """Get all orders that are expecting deliveries."""
        pending_orders = (
            PurchaseOrder.objects.filter(
                status__in=[
                    PurchaseOrder.Status.SENT,
                    PurchaseOrder.Status.CONFIRMED,
                    PurchaseOrder.Status.PARTIALLY_RECEIVED,
                ]
            )
            .select_related("supplier")
            .prefetch_related("items__item_supplier__item")
        )

        order_data = []
        for order in pending_orders:
            order_data.append(
                {
                    "id": order.id,
                    "po_number": order.po_number,
                    "supplier_name": order.supplier.name,
                    "status": order.status,
                    "expected_delivery_date": order.expected_delivery_date,
                    "days_since_ordered": order.days_since_ordered,
                    "total_items": order.total_items,
                    # What this order is still waiting on, off the lines
                    # receiving has not finished with. It used to subtract the
                    # order's received total from its ordered total, which
                    # counts the two over different sets of lines — the received
                    # side includes struck-off ones — and goes on reporting the
                    # shortfall of a line written off as never arriving as if it
                    # were still in transit.
                    "items_pending": sum(line.quantity_pending for line in order.outstanding_items),
                    "estimated_total": order.estimated_total,
                }
            )

        return Response(order_data)


class AnalyticsViewSet(viewsets.ViewSet):
    """Analytics and reporting endpoints."""

    permission_classes = [IsAuthenticated]

    def get_transparency_queryset(self):
        """Base queryset for transparency data with related objects optimized."""
        return (
            ReorderRequest.objects.select_related("item", "item__category")
            .prefetch_related(item_suppliers_prefetch("item__item_suppliers"))
            .all()
        )

    @action(detail=False, methods=["get"])
    def supplier_performance(self, request):
        """Get supplier performance metrics."""
        suppliers_data = []

        # Get all suppliers with orders
        from inventory.models import Supplier

        suppliers = Supplier.objects.filter(purchase_orders__isnull=False).distinct()

        for supplier in suppliers:
            # Order metrics
            orders = supplier.purchase_orders.all()
            total_orders = orders.count()
            completed_orders = orders.filter(status=PurchaseOrder.Status.RECEIVED).count()
            active_orders = orders.exclude(
                status__in=[PurchaseOrder.Status.RECEIVED, PurchaseOrder.Status.CANCELLED]
            ).count()

            # Lead time metrics
            lead_time_logs = LeadTimeLog.objects.filter(item_supplier__supplier=supplier)

            avg_lead_time = lead_time_logs.aggregate(avg=Avg("actual_lead_time_days"))["avg"] or 0

            total_deliveries = lead_time_logs.count()
            on_time_deliveries = lead_time_logs.filter(variance_days__lte=0).count()
            early_deliveries = lead_time_logs.filter(variance_days__lt=0).count()
            late_deliveries = lead_time_logs.filter(variance_days__gt=0).count()

            on_time_rate = (
                (on_time_deliveries / total_deliveries * 100) if total_deliveries > 0 else 0
            )
            early_rate = (early_deliveries / total_deliveries * 100) if total_deliveries > 0 else 0
            late_rate = (late_deliveries / total_deliveries * 100) if total_deliveries > 0 else 0

            # Financial metrics
            total_value = orders.aggregate(total=Sum("estimated_total"))["total"] or 0

            # Quality metrics
            delivered_items = DeliveryItem.objects.filter(
                purchase_order_item__purchase_order__supplier=supplier
            )
            total_items_delivered = delivered_items.count()
            damaged_items = delivered_items.filter(is_damaged=True).count()
            damage_rate = (
                (damaged_items / total_items_delivered * 100) if total_items_delivered > 0 else 0
            )

            # Recent activity
            last_order = orders.order_by("-order_date").first()
            last_order_date = last_order.order_date if last_order else None
            days_since_last_order = None
            if last_order_date:
                days_since_last_order = (timezone.now() - last_order_date).days

            suppliers_data.append(
                SupplierPerformanceSerializer(
                    {
                        "supplier_id": supplier.id,
                        "supplier_name": supplier.name,
                        "total_orders": total_orders,
                        "completed_orders": completed_orders,
                        "active_orders": active_orders,
                        "average_lead_time_days": avg_lead_time,
                        "on_time_delivery_rate": on_time_rate,
                        "early_delivery_rate": early_rate,
                        "late_delivery_rate": late_rate,
                        "total_order_value": total_value,
                        "damage_rate": damage_rate,
                        "last_order_date": last_order_date,
                        "days_since_last_order": days_since_last_order,
                    }
                ).data
            )

        # Sort by total order value descending
        suppliers_data.sort(key=lambda x: x["total_order_value"], reverse=True)

        return Response(suppliers_data)

    @action(detail=False, methods=["get"])
    def lead_time_trends(self, request):
        """Get lead time trends over the past 6 months."""
        six_months_ago = timezone.now() - timedelta(days=180)

        # Get lead time data by month
        from django.db.models import Extract

        monthly_data = (
            LeadTimeLog.objects.filter(actual_delivery_date__gte=six_months_ago.date())
            .annotate(
                month=Extract("actual_delivery_date", "month"),
                year=Extract("actual_delivery_date", "year"),
            )
            .values("year", "month")
            .annotate(
                avg_lead_time=Avg("actual_lead_time_days"),
                avg_variance=Avg("variance_days"),
                total_deliveries=Count("id"),
                on_time_deliveries=Count("id", filter=Q(variance_days__lte=0)),
            )
            .order_by("year", "month")
        )

        trend_data = []
        for data in monthly_data:
            on_time_rate = (data["on_time_deliveries"] / data["total_deliveries"]) * 100
            trend_data.append(
                {
                    "month": f"{data['year']}-{data['month']:02d}",
                    "average_lead_time_days": round(data["avg_lead_time"], 1),
                    "average_variance_days": round(data["avg_variance"], 1),
                    "total_deliveries": data["total_deliveries"],
                    "on_time_delivery_rate": round(on_time_rate, 1),
                }
            )

        return Response(trend_data)

    @action(detail=False, methods=["get"], permission_classes=[AllowAny], authentication_classes=[])
    def transparency(self, request):
        """
        Public transparency endpoint showing financial information about orders.

        Open by default for makerspace transparency - shows costs, invoices,
        purchase orders, and delivery information for community visibility.
        """
        try:
            # Get orders with transparency data (recent first)
            transparency_orders = (
                self.get_transparency_queryset()
                .filter(
                    models.Q(actual_cost__isnull=False)
                    | models.Q(invoice_number__isnull=False)
                    | models.Q(invoice_url__isnull=False)
                    | models.Q(purchase_order_url__isnull=False)
                    | models.Q(delivery_tracking_url__isnull=False)
                    | models.Q(order_number__isnull=False)
                )
                .exclude(
                    models.Q(actual_cost__isnull=True)
                    & models.Q(invoice_number="")
                    & models.Q(invoice_url="")
                    & models.Q(purchase_order_url="")
                    & models.Q(delivery_tracking_url="")
                    & models.Q(order_number="")
                )
                .order_by("-ordered_at", "-requested_at")[
                    :100
                ]  # Last 100 orders with financial data
            )

            transparency_data = []
            total_spent = Decimal("0.00")
            ledger_entries = []

            for order in transparency_orders:
                if order.actual_cost:
                    total_spent += order.actual_cost

                supplier = order.item.supplier
                supplier_name = supplier.name if supplier else None

                # Public transparency information
                order_data = {
                    "id": order.id,
                    "item_id": str(order.item.id),
                    "item_name": order.item.name,
                    "item_category": (order.item.category.name if order.item.category else None),
                    "quantity_ordered": order.quantity,
                    "status": order.status,
                    "requested_at": order.requested_at.isoformat(),
                    "ordered_at": (order.ordered_at.isoformat() if order.ordered_at else None),
                    "delivered_at": (
                        order.actual_delivery.isoformat() if order.actual_delivery else None
                    ),
                    # Financial transparency
                    "estimated_cost": (
                        None if order.estimated_cost is None else float(order.estimated_cost)
                    ),
                    "actual_cost": (float(order.actual_cost) if order.actual_cost else None),
                    "cost_per_unit": (float(order.cost_per_unit) if order.cost_per_unit else None),
                    "cost_variance": (
                        float(order.actual_cost - order.estimated_cost)
                        if (order.actual_cost and order.estimated_cost is not None)
                        else None
                    ),
                    # Document links
                    "order_number": order.order_number,
                    "invoice_number": order.invoice_number,
                    "invoice_url": order.invoice_url,
                    "purchase_order_url": order.purchase_order_url,
                    "delivery_tracking_url": order.delivery_tracking_url,
                    "supplier_url": order.supplier_url,
                    # Public notes
                    "public_notes": order.public_notes,
                    # Supplier info
                    "supplier_name": supplier_name,
                }

                transparency_data.append(order_data)

                ledger_entries.append(
                    {
                        "id": order.id,
                        "item_id": str(order.item.id),
                        "item_name": order.item.name,
                        "supplier_name": supplier_name,
                        "quantity": order.quantity,
                        "requested_at": order.requested_at.isoformat(),
                        "ordered_at": (order.ordered_at.isoformat() if order.ordered_at else None),
                        "delivered_at": (
                            order.actual_delivery.isoformat() if order.actual_delivery else None
                        ),
                        "actual_cost": (float(order.actual_cost) if order.actual_cost else None),
                        "estimated_cost": (
                            None if order.estimated_cost is None else float(order.estimated_cost)
                        ),
                        "status": order.status,
                        "order_number": order.order_number,
                        "invoice_number": order.invoice_number,
                    }
                )

            # Get purchase orders for transparency
            purchase_orders = (
                PurchaseOrder.objects.filter(
                    status__in=[
                        PurchaseOrder.Status.SENT,
                        PurchaseOrder.Status.CONFIRMED,
                        PurchaseOrder.Status.PARTIALLY_RECEIVED,
                        PurchaseOrder.Status.RECEIVED,
                    ]
                )
                .select_related("supplier")
                .prefetch_related("items__item_supplier__item", "items__asset")
                .order_by("-order_date")[:50]  # Last 50 purchase orders
            )

            po_transparency_data = []
            po_total_spent = Decimal("0.00")

            for po in purchase_orders:
                if po.actual_total:
                    po_total_spent += po.actual_total

                # Count items (excluding voided)
                active_items = po.items.filter(is_voided=False)
                total_items = active_items.count()
                total_quantity = sum(item.quantity_ordered for item in active_items)

                po_data = {
                    "id": str(po.id),
                    "po_number": po.po_number,
                    "supplier_name": po.supplier.name,
                    "status": po.status,
                    "status_label": po.get_status_display(),
                    "order_date": po.order_date.isoformat(),
                    "expected_delivery_date": (
                        po.expected_delivery_date.isoformat() if po.expected_delivery_date else None
                    ),
                    "estimated_total": (
                        None if po.estimated_total is None else float(po.estimated_total)
                    ),
                    "actual_total": float(po.actual_total) if po.actual_total else None,
                    "total_items": total_items,
                    "total_quantity": total_quantity,
                    "is_fully_received": po.is_fully_received,
                }

                po_transparency_data.append(po_data)

            summary = {
                "total_orders_with_financial_data": len(transparency_data),
                "total_amount_spent": float(total_spent),
                "total_purchase_orders": len(po_transparency_data),
                "total_po_amount_spent": float(po_total_spent),
                "last_updated": timezone.now().isoformat(),
                "transparency_note": "Dallas Makerspace operates with full financial transparency. All purchase information is publicly available.",
            }

            return Response(
                {
                    "summary": summary,
                    "orders": transparency_data,
                    "ledger": ledger_entries,
                    "purchase_orders": po_transparency_data,
                }
            )

        except Exception as e:
            return Response(
                {"error": "Unable to fetch transparency data", "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=False, methods=["get"], permission_classes=[AllowAny], authentication_classes=[])
    def logistics_dashboard(self, request):
        """
        Public endpoint providing data for the logistics TV dashboard.
        Optimized for Fire TV / Silk browser display.

        Returns:
        - Number of Open Item Requests
        - Number of Open Locations with Problems Reported
        - Number of Assets with Overdue Maintenance
        - QR Code Scans in last 7 days (with daily breakdown for sparkline)
        """
        from datetime import timedelta

        from django.db.models import Count
        from django.db.models.functions import TruncDate

        from inventory.models import Asset, AssetPart, LocationProblem
        from location_checkins.models import LocationFeedback, LocationTask, SecurityReport

        # 1. Number of Open Item Requests
        # Open requests are those that are pending or approved (not yet ordered/received/cancelled)
        # Using string literals to ensure we match the actual database values
        open_item_requests = ReorderRequest.objects.filter(
            status__in=["pending", "approved"]
        ).count()

        # 2. Number of Open Locations with Problems Reported
        # Count unique locations that have unresolved tasks, security reports, or negative feedback
        locations_with_tasks = (
            LocationTask.objects.filter(status__in=["pending", "in_progress"])
            .values_list("location_id", flat=True)
            .distinct()
        )

        locations_with_security = (
            SecurityReport.objects.filter(is_resolved=False)
            .values_list("location_id", flat=True)
            .distinct()
        )

        locations_with_feedback = (
            LocationFeedback.objects.filter(feedback_type="negative", is_resolved=False)
            .values_list("location_id", flat=True)
            .distinct()
        )

        # Locations with open LocationProblem reports (oms-0yz). Anything still
        # in REPORTED/IN_PROGRESS counts toward the dashboard's problem total.
        open_location_problems_qs = LocationProblem.objects.filter(
            status__in=[LocationProblem.Status.REPORTED, LocationProblem.Status.IN_PROGRESS]
        )
        locations_with_lp = open_location_problems_qs.values_list(
            "location_id", flat=True
        ).distinct()

        # Combine all unique location IDs
        all_problem_location_ids = set(
            list(locations_with_tasks)
            + list(locations_with_security)
            + list(locations_with_feedback)
            + list(locations_with_lp)
        )
        open_locations_with_problems = len(all_problem_location_ids)

        # Urgent / high severity open problems trigger the dashboard alert mode.
        urgent_location_problems = open_location_problems_qs.filter(
            severity__in=[
                LocationProblem.Severity.HIGH,
                LocationProblem.Severity.URGENT,
            ]
        ).count()
        alert_active = urgent_location_problems > 0

        # 3. Number of Assets with Overdue Maintenance
        # Assets with parts that need replacement (calculated property)
        # We need to check parts that have maintenance_interval_days and last_replaced_at
        # and where days_since_replacement >= maintenance_interval_days
        parts_with_intervals = AssetPart.objects.filter(
            maintenance_interval_days__isnull=False, last_replaced_at__isnull=False
        ).select_related("asset")

        overdue_asset_ids = set()
        for part in parts_with_intervals:
            if part.needs_replacement:
                overdue_asset_ids.add(part.asset_id)

        overdue_maintenance_count = len(overdue_asset_ids)

        # Also count MaintenanceItems that are overdue or due this week
        from inventory.models import MaintenanceItem

        now_dt = timezone.now()
        week_out = now_dt + timedelta(days=7)
        pm_due_this_week = 0
        pm_overdue = 0
        for mi in MaintenanceItem.objects.filter(is_active=True, interval_days__isnull=False):
            if mi.is_overdue:
                pm_overdue += 1
            elif mi.next_due_at and mi.next_due_at <= week_out:
                pm_due_this_week += 1

        # 4. QR Code Scans in last 7 days with daily breakdown
        # Note: We count unique assets and inventory items scanned per day
        # If an item is scanned multiple times in a day, we only count it once for that day
        seven_days_ago = timezone.now() - timedelta(days=7)

        # Get all assets scanned in last 7 days, grouped by date
        asset_scans_by_date = (
            Asset.objects.filter(last_scanned_at__gte=seven_days_ago)
            .exclude(last_scanned_at__isnull=True)
            .annotate(scan_date=TruncDate("last_scanned_at"))
            .values("scan_date")
            .annotate(count=Count("id", distinct=True))
            .order_by("scan_date")
        )

        # Get all inventory items scanned in last 7 days, grouped by date
        from inventory.models import InventoryItem

        item_scans_by_date = (
            InventoryItem.objects.filter(last_scanned_at__gte=seven_days_ago)
            .exclude(last_scanned_at__isnull=True)
            .annotate(scan_date=TruncDate("last_scanned_at"))
            .values("scan_date")
            .annotate(count=Count("id", distinct=True))
            .order_by("scan_date")
        )

        # Combine asset and inventory item scans by date
        today = timezone.now().date()
        scan_data = {}

        # Process asset scans
        for scan in asset_scans_by_date:
            scan_date = scan["scan_date"]
            # TruncDate returns a date object, but handle both cases
            if isinstance(scan_date, str):
                from datetime import datetime

                try:
                    scan_date = datetime.fromisoformat(scan_date).date()
                except (ValueError, AttributeError):
                    continue
            elif hasattr(scan_date, "date"):
                scan_date = scan_date.date()
            scan_data[scan_date] = scan_data.get(scan_date, 0) + scan["count"]

        # Process inventory item scans
        for scan in item_scans_by_date:
            scan_date = scan["scan_date"]
            # TruncDate returns a date object, but handle both cases
            if isinstance(scan_date, str):
                from datetime import datetime

                try:
                    scan_date = datetime.fromisoformat(scan_date).date()
                except (ValueError, AttributeError):
                    continue
            elif hasattr(scan_date, "date"):
                scan_date = scan_date.date()
            scan_data[scan_date] = scan_data.get(scan_date, 0) + scan["count"]

        # Build array for last 7 days (including today)
        qr_scans_by_day = []
        total_qr_scans = 0
        for i in range(6, -1, -1):  # 6 days ago to today
            date = today - timedelta(days=i)
            count = scan_data.get(date, 0)
            qr_scans_by_day.append({"date": date.isoformat(), "count": count})
            total_qr_scans += count

        return Response(
            {
                "open_item_requests": open_item_requests,
                "open_locations_with_problems": open_locations_with_problems,
                "urgent_location_problems": urgent_location_problems,
                "alert_active": alert_active,
                "assets_overdue_maintenance": overdue_maintenance_count,
                "pm_overdue": pm_overdue,
                "pm_due_this_week": pm_due_this_week,
                "qr_scans_total": total_qr_scans,
                "qr_scans_by_day": qr_scans_by_day,
                "last_updated": timezone.now().isoformat(),
            }
        )


class WebHookViewSet(viewsets.ModelViewSet):
    """
    API endpoint for webhook configurations.

    Allows authenticated users to manage webhooks and test them.
    """

    authentication_classes = (JWTAuthentication,)
    permission_classes = [IsAuthenticated]
    queryset = WebHook.objects.all()
    serializer_class = WebHookSerializer

    def get_serializer_class(self):
        """Use create serializer for POST requests."""
        if self.action == "create":
            return WebHookCreateSerializer
        return WebHookSerializer

    def perform_create(self, serializer):
        webhook = serializer.save()
        record_webhook_audit_event(
            action=WebhookAuditEvent.Action.WEBHOOK_CREATE,
            actor=self.request.user,
            webhook=webhook,
            metadata={
                "name": webhook.name,
                "url": webhook.url,
                "event_type": webhook.event_type,
            },
        )

    def perform_update(self, serializer):
        # Snapshot pre-save attrs so we can detect:
        #   * config changes (webhook_update with diff)
        #   * is_active flip (webhook_disable / webhook_enable)
        #   * secret rotation (webhook_secret_rotate; value never recorded)
        instance = serializer.instance
        before_attrs = {name: getattr(instance, name) for name in WebhookAuditEvent.AUDITED_FIELDS}
        before_secret = instance.secret
        before_active = instance.is_active

        webhook = serializer.save()

        if webhook.secret != before_secret:
            record_webhook_audit_event(
                action=WebhookAuditEvent.Action.WEBHOOK_SECRET_ROTATE,
                actor=self.request.user,
                webhook=webhook,
            )

        # Build a synthetic 'before' for the diff helper. Using attribute
        # cloning avoids hitting the DB again and keeps the helper pure.
        synthetic_before = WebHook(**before_attrs)
        changes = diff_webhook_audited_fields(synthetic_before, webhook)
        # is_active flips are surfaced as their own action — pull them out
        # of the generic 'webhook_update' diff so reviewers see the
        # lifecycle event explicitly.
        is_active_changed = "is_active" in changes
        if is_active_changed:
            changes.pop("is_active")
        if changes:
            record_webhook_audit_event(
                action=WebhookAuditEvent.Action.WEBHOOK_UPDATE,
                actor=self.request.user,
                webhook=webhook,
                metadata={"changes": changes},
            )
        if before_active != webhook.is_active:
            record_webhook_audit_event(
                action=(
                    WebhookAuditEvent.Action.WEBHOOK_ENABLE
                    if webhook.is_active
                    else WebhookAuditEvent.Action.WEBHOOK_DISABLE
                ),
                actor=self.request.user,
                webhook=webhook,
            )

    def perform_destroy(self, instance):
        # Record audit BEFORE delete so the metadata captures identifying
        # fields while we still have the row. The webhook FK on the audit
        # row is SET_NULL on cascade, so the audit row survives the delete.
        record_webhook_audit_event(
            action=WebhookAuditEvent.Action.WEBHOOK_DELETE,
            actor=self.request.user,
            webhook=instance,
            metadata={
                "name": instance.name,
                "url": instance.url,
                "event_type": instance.event_type,
            },
        )
        instance.delete()

    @action(detail=True, methods=["post"], url_path="test", url_name="test")
    def test(self, request, pk=None):
        """
        Test a webhook by sending a test payload via Celery task.

        This queues a Celery task to send the webhook and returns the task ID
        for status tracking.
        """
        from celery import current_app

        from .tasks import run_webhook_test

        webhook = self.get_object()

        # Queue the webhook test task
        if current_app.conf.task_always_eager:
            # In eager mode (tests), run synchronously
            task_result = run_webhook_test.run(webhook.id)
            result = {
                "webhook_id": webhook.id,
                "webhook_name": webhook.name,
                "task_id": None,
                "task_status": "SUCCESS" if task_result.get("success") else "FAILURE",
                "success": task_result.get("success", False),
                "status_code": task_result.get("status_code"),
                "response_time_ms": task_result.get("response_time_ms"),
                "response_body": task_result.get("response_body"),
                "error_message": task_result.get("error_message"),
                "tested_at": task_result.get("tested_at", timezone.now().isoformat()),
            }
        else:
            # In production, queue the task
            task = run_webhook_test.delay(webhook.id)
            result = {
                "webhook_id": webhook.id,
                "webhook_name": webhook.name,
                "task_id": task.id,
                "task_status": "PENDING",
                "success": None,
                "status_code": None,
                "response_time_ms": None,
                "response_body": None,
                "error_message": None,
                "tested_at": None,
            }

        serializer = WebHookTestResultSerializer(result)
        return Response(serializer.data, status=status.HTTP_202_ACCEPTED)

    @action(detail=False, methods=["get"], url_path="test-status", url_name="test-status")
    def test_status(self, request):
        """
        Get the status of a webhook test task.

        Query parameter: task_id - The Celery task ID to check

        Returns the task result when available, or task status if still pending.
        """
        from celery.result import AsyncResult
        from django_celery_results.models import TaskResult

        task_id = request.query_params.get("task_id")
        if not task_id:
            return Response(
                {"error": "task_id query parameter is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # Try to get result from database first (more reliable)
            try:
                task_result = TaskResult.objects.get(task_id=task_id)
                task_state = task_result.status
                if task_result.result:
                    import json

                    try:
                        result_data = (
                            json.loads(task_result.result)
                            if isinstance(task_result.result, str)
                            else task_result.result
                        )
                    except (json.JSONDecodeError, TypeError):
                        result_data = task_result.result
                else:
                    result_data = None
            except TaskResult.DoesNotExist:
                # Fallback to AsyncResult if not in database yet
                async_result = AsyncResult(task_id)
                task_state = async_result.state
                result_data = async_result.result if async_result.ready() else None

            # Map Celery states to our status
            status_mapping = {
                "PENDING": "PENDING",
                "STARTED": "PENDING",
                "RETRY": "PENDING",
                "SUCCESS": "SUCCESS",
                "FAILURE": "FAILURE",
                "REVOKED": "FAILURE",
            }
            task_status = status_mapping.get(task_state, "UNKNOWN")

            # If task is complete, format the result
            if task_state in ["SUCCESS", "FAILURE"] and result_data:
                if isinstance(result_data, dict):
                    result = {
                        "webhook_id": result_data.get("webhook_id"),
                        "webhook_name": result_data.get("webhook_name"),
                        "task_id": task_id,
                        "task_status": task_status,
                        "success": result_data.get("success", False),
                        "status_code": result_data.get("status_code"),
                        "response_time_ms": result_data.get("response_time_ms"),
                        "response_body": result_data.get("response_body"),
                        "error_message": result_data.get("error_message"),
                        "tested_at": result_data.get("tested_at"),
                    }
                else:
                    # Handle unexpected result format
                    result = {
                        "task_id": task_id,
                        "task_status": task_status,
                        "success": False,
                        "error_message": f"Unexpected result format: {type(result_data)}",
                    }
            else:
                # Task still pending
                result = {
                    "task_id": task_id,
                    "task_status": task_status,
                    "success": None,
                    "status_code": None,
                    "response_time_ms": None,
                    "response_body": None,
                    "error_message": None,
                    "tested_at": None,
                }

            serializer = WebHookTestResultSerializer(result)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {"error": f"Failed to get task status: {str(e)}", "task_id": task_id},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


def _as_float(amount):
    """A money figure as a JSON number, or ``None`` when there is no figure.

    The purchasing reports are declared in floats (ScanTTY reads them as
    ``float64``), and every one of them used to spell the null guard
    ``float(x or 0)`` — which reports "$0.00" for a price nobody recorded, for
    a supplier that charges nothing, and for an item with no price history at
    all. This keeps the absence an absence (op-9m2v); a recorded ``0.00`` comes
    through as ``0.0``, which is what it is.
    """
    return None if amount is None else float(amount)


def _money_cell(amount):
    """A money figure as a CSV cell, or an EMPTY cell where there is none.

    The export twin of :func:`_as_float`, and the backend twin of
    ``csvExport.ts``'s ``reportMoney``. ``f"{x:.2f}"`` on a ``None`` raises —
    the three cost columns of the price-trend export became nullable in the
    same commit that made ``latest_unit_cost`` honest — and ``f"{x or 0:.2f}"``
    would be the falsy guard again, one layer along: a blank sums as nothing
    AND reads as nothing, whereas "0.00" makes a spreadsheet count the unknowns
    as free. A recorded ``0.00`` still exports as ``0.00`` (op-9m2v).
    """
    return "" if amount is None else f"{amount:.2f}"


class PurchasingReportViewSet(viewsets.ViewSet):
    """API endpoint for purchasing reports."""

    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["get"])
    def spend_by_supplier(self, request):
        """Get total spend per supplier from purchase orders."""
        queryset = (
            PurchaseOrder.objects.filter(status=PurchaseOrder.Status.RECEIVED)
            .select_related("supplier")
            .values("supplier__id", "supplier__name")
            .annotate(
                total_orders=Count("id"),
                total_spend=Sum("actual_total"),
                avg_order_value=Avg("actual_total"),
            )
            .order_by("-total_spend")
        )

        data = []
        for item in queryset:
            data.append(
                {
                    "supplier_id": item["supplier__id"],
                    "supplier_name": item["supplier__name"],
                    "total_orders": item["total_orders"],
                    "total_spend": float(item["total_spend"] or 0),
                    "avg_order_value": float(item["avg_order_value"] or 0),
                }
            )

        return Response(data)

    @action(detail=False, methods=["get"])
    def spend_by_category(self, request):
        """Get total spend grouped by item category."""
        queryset = (
            PurchaseOrderItem.objects.filter(
                purchase_order__status=PurchaseOrder.Status.RECEIVED,
                item_supplier__isnull=False,
            )
            .select_related("item_supplier__item__category", "purchase_order")
            .values("item_supplier__item__category__id", "item_supplier__item__category__name")
            .annotate(
                total_items=Count("id"),
                total_quantity=Sum("quantity_received"),
                total_spend=Sum(F("quantity_received") * F("unit_cost_actual")),
            )
            .order_by("-total_spend")
        )

        data = []
        for item in queryset:
            data.append(
                {
                    "category_id": item["item_supplier__item__category__id"],
                    "category_name": item["item_supplier__item__category__name"] or "Uncategorized",
                    "total_items": item["total_items"],
                    "total_quantity": item["total_quantity"] or 0,
                    "total_spend": float(item["total_spend"] or 0),
                }
            )

        return Response(data)

    @action(detail=False, methods=["get"])
    def lead_time_analysis(self, request):
        """Get lead time analysis from LeadTimeLog data."""
        from datetime import datetime, timedelta

        # Get date range from query params (default: last 6 months)
        start_date_str = request.query_params.get("start_date")
        end_date_str = request.query_params.get("end_date")

        if start_date_str and end_date_str:
            try:
                start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            except ValueError:
                # Fall back to default if date parsing fails
                start_date = (timezone.now() - timedelta(days=6 * 30)).date()
                end_date = timezone.now().date()
        else:
            # Fall back to months parameter for backward compatibility
            months = int(request.query_params.get("months", 6))
            start_date = (timezone.now() - timedelta(days=months * 30)).date()
            end_date = timezone.now().date()

        queryset = (
            LeadTimeLog.objects.filter(
                actual_delivery_date__gte=start_date,
                actual_delivery_date__lte=end_date,
            )
            .select_related("item_supplier__supplier", "item_supplier__item")
            .values(
                "item_supplier__supplier__id",
                "item_supplier__supplier__name",
                "item_supplier__item__name",
            )
            .annotate(
                total_orders=Count("id"),
                avg_estimated_lead_time=Avg("estimated_lead_time_days"),
                avg_actual_lead_time=Avg("actual_lead_time_days"),
                avg_variance=Avg("variance_days"),
                on_time_count=Count("id", filter=Q(variance_days__lte=0)),
            )
            .order_by("-total_orders")
        )

        data = []
        for item in queryset:
            total = item["total_orders"]
            on_time_rate = (item["on_time_count"] / total * 100) if total > 0 else 0

            data.append(
                {
                    "supplier_id": item["item_supplier__supplier__id"],
                    "supplier_name": item["item_supplier__supplier__name"],
                    "item_name": item["item_supplier__item__name"],
                    "total_orders": total,
                    "avg_estimated_lead_time": round(item["avg_estimated_lead_time"] or 0, 1),
                    "avg_actual_lead_time": round(item["avg_actual_lead_time"] or 0, 1),
                    "avg_variance": round(item["avg_variance"] or 0, 1),
                    "on_time_rate": round(on_time_rate, 1),
                }
            )

        return Response(data)

    @action(detail=False, methods=["get"])
    def price_trends(self, request):
        """Get price trends from PriceHistory data."""
        from datetime import datetime, timedelta

        from inventory.models import PriceHistory

        # Get date range from query params (default: last 12 months)
        start_date_str = request.query_params.get("start_date")
        end_date_str = request.query_params.get("end_date")

        if start_date_str and end_date_str:
            try:
                start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            except ValueError:
                # Fall back to default if date parsing fails
                start_date = (timezone.now() - timedelta(days=12 * 30)).date()
                end_date = timezone.now().date()
        else:
            # Fall back to months parameter for backward compatibility
            months = int(request.query_params.get("months", 12))
            start_date = (timezone.now() - timedelta(days=months * 30)).date()
            end_date = timezone.now().date()

        # Get price history grouped by item
        queryset = (
            PriceHistory.objects.filter(
                recorded_at__date__gte=start_date,
                recorded_at__date__lte=end_date,
            )
            .select_related("item_supplier__item", "item_supplier__supplier")
            .values(
                "item_supplier__item__id",
                "item_supplier__item__name",
                "item_supplier__supplier__id",
                "item_supplier__supplier__name",
            )
            .annotate(
                price_changes=Count("id"),
                min_unit_cost=Min("unit_cost"),
                max_unit_cost=Max("unit_cost"),
            )
            .order_by("-price_changes")
        )

        data = []
        for item in queryset:
            # Get the actual latest price
            latest_price = (
                PriceHistory.objects.filter(
                    item_supplier__item__id=item["item_supplier__item__id"],
                    item_supplier__supplier__id=item.get("item_supplier__supplier__id"),
                )
                .order_by("-recorded_at")
                .first()
            )

            # Calculate price change percentage if we have multiple records
            price_change_pct = None
            if item["price_changes"] > 1:
                first_price = (
                    PriceHistory.objects.filter(
                        item_supplier__item__id=item["item_supplier__item__id"],
                        item_supplier__supplier__id=item.get("item_supplier__supplier__id"),
                    )
                    .order_by("recorded_at")
                    .first()
                )
                first = unit_price_of(first_price)
                latest = unit_price_of(latest_price)
                # ``first.amount`` of 0.00 leaves the percentage undefined
                # (nothing to divide by) — but a drop TO 0.00 is a real -100%,
                # and base's ``and latest_price.unit_cost`` swallowed it, so a
                # supplier that started donating an item reported no change
                # (op-9m2v). Same rule as ``PriceHistory.price_change_percentage``.
                if first.is_known and latest.is_known and first.amount != 0:
                    change = ((latest.amount - first.amount) / first.amount) * 100
                    price_change_pct = round(change, 2)

            data.append(
                {
                    "item_id": str(item["item_supplier__item__id"]),
                    "item_name": item["item_supplier__item__name"],
                    "supplier_name": item["item_supplier__supplier__name"],
                    "price_changes": item["price_changes"],
                    # ``null``, not 0, where nothing is recorded: base
                    # reported "$0.00" for a supplier with no price on file
                    # AND for one that charges nothing AND — on
                    # ``latest_unit_cost`` — for an item with no price history
                    # at all, three different facts collapsed onto one number
                    # in a report whose whole subject is price (op-9m2v).
                    "min_unit_cost": _as_float(item["min_unit_cost"]),
                    "max_unit_cost": _as_float(item["max_unit_cost"]),
                    "latest_unit_cost": _as_float(unit_price_of(latest_price).amount),
                    "price_change_percentage": price_change_pct,
                }
            )

        return Response(data)

    @action(detail=False, methods=["get"])
    def export(self, request):
        """Export purchasing report data as CSV."""
        import csv

        from django.http import HttpResponse

        report_type = request.query_params.get("type", "spend_by_supplier")

        if report_type == "spend_by_supplier":
            response = self.spend_by_supplier(request)
            data = response.data

            response_obj = HttpResponse(content_type="text/csv")
            response_obj["Content-Disposition"] = (
                'attachment; filename="purchasing_spend_by_supplier.csv"'
            )

            writer = csv.DictWriter(
                response_obj,
                fieldnames=["supplier_name", "total_orders", "total_spend", "avg_order_value"],
            )
            writer.writeheader()
            for row in data:
                writer.writerow(
                    {
                        "supplier_name": row["supplier_name"],
                        "total_orders": row["total_orders"],
                        "total_spend": f"{row['total_spend']:.2f}",
                        "avg_order_value": f"{row['avg_order_value']:.2f}",
                    }
                )

            return response_obj
        elif report_type == "spend_by_category":
            response = self.spend_by_category(request)
            data = response.data

            response_obj = HttpResponse(content_type="text/csv")
            response_obj["Content-Disposition"] = (
                'attachment; filename="purchasing_spend_by_category.csv"'
            )

            writer = csv.DictWriter(
                response_obj,
                fieldnames=["category_name", "total_items", "total_quantity", "total_spend"],
            )
            writer.writeheader()
            for row in data:
                writer.writerow(
                    {
                        "category_name": row["category_name"],
                        "total_items": row["total_items"],
                        "total_quantity": row["total_quantity"],
                        "total_spend": f"{row['total_spend']:.2f}",
                    }
                )

            return response_obj
        elif report_type == "lead_time_analysis":
            response = self.lead_time_analysis(request)
            data = response.data

            response_obj = HttpResponse(content_type="text/csv")
            response_obj["Content-Disposition"] = (
                'attachment; filename="purchasing_lead_time_analysis.csv"'
            )

            writer = csv.DictWriter(
                response_obj,
                fieldnames=[
                    "supplier_name",
                    "item_name",
                    "total_orders",
                    "avg_estimated_lead_time",
                    "avg_actual_lead_time",
                    "avg_variance",
                    "on_time_rate",
                ],
            )
            writer.writeheader()
            for row in data:
                writer.writerow(
                    {
                        "supplier_name": row["supplier_name"],
                        "item_name": row["item_name"],
                        "total_orders": row["total_orders"],
                        "avg_estimated_lead_time": row["avg_estimated_lead_time"],
                        "avg_actual_lead_time": row["avg_actual_lead_time"],
                        "avg_variance": row["avg_variance"],
                        "on_time_rate": f"{row['on_time_rate']:.1f}%",
                    }
                )

            return response_obj
        elif report_type == "price_trends":
            response = self.price_trends(request)
            data = response.data

            response_obj = HttpResponse(content_type="text/csv")
            response_obj["Content-Disposition"] = (
                'attachment; filename="purchasing_price_trends.csv"'
            )

            writer = csv.DictWriter(
                response_obj,
                fieldnames=[
                    "item_name",
                    "supplier_name",
                    "price_changes",
                    "min_unit_cost",
                    "max_unit_cost",
                    "latest_unit_cost",
                    "price_change_percentage",
                ],
            )
            writer.writeheader()
            for row in data:
                writer.writerow(
                    {
                        "item_name": row["item_name"],
                        "supplier_name": row["supplier_name"],
                        "price_changes": row["price_changes"],
                        "min_unit_cost": _money_cell(row["min_unit_cost"]),
                        "max_unit_cost": _money_cell(row["max_unit_cost"]),
                        "latest_unit_cost": _money_cell(row["latest_unit_cost"]),
                        # ``is None``, not truthiness: a price that did not
                        # move is a 0.00% change and a fact, and the falsy
                        # spelling exported it as the same blank an
                        # incomputable percentage gets (op-9m2v).
                        "price_change_percentage": (
                            ""
                            if row["price_change_percentage"] is None
                            else f"{row['price_change_percentage']:.2f}%"
                        ),
                    }
                )

            return response_obj

        return Response({"error": "Invalid report type"}, status=status.HTTP_400_BAD_REQUEST)
