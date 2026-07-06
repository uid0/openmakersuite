"""
Views for reorder queue API.
"""

from datetime import timedelta
from decimal import Decimal

from django.db import models, transaction
from django.db.models import Avg, Count, F, Max, Min, Q, Sum
from django.utils import timezone

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from inventory.models import InventoryItem

from .audit import record_event as record_audit_event
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
    BarcodeReceiptSerializer,
    MarkDeliveredSerializer,
    OrderDeliverySerializer,
    OrderMetricsSerializer,
    PurchaseOrderAttachmentSerializer,
    PurchaseOrderCreateSerializer,
    PurchaseOrderSerializer,
    ReceiveItemsSerializer,
    ReorderRequestCreateSerializer,
    ReorderRequestSerializer,
    SupplierPerformanceSerializer,
    WebHookCreateSerializer,
    WebHookSerializer,
    WebHookTestResultSerializer,
)
from .webhook_audit import diff_audited_fields as diff_webhook_audited_fields
from .webhook_audit import record_event as record_webhook_audit_event


def _create_lead_time_log(po_item, delivery_date):
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


def _apply_po_receipt(
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

    Callers are responsible for validating ``line_quantities`` and for wrapping
    the call in a transaction. Returns the created :class:`OrderDelivery`.
    """
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
            _create_lead_time_log(po_item, delivery.delivery_date)

    if purchase_order.is_fully_received:
        purchase_order.status = PurchaseOrder.RECEIVED
    else:
        purchase_order.status = PurchaseOrder.PARTIALLY_RECEIVED
    purchase_order.save()

    delivery.is_complete = purchase_order.is_fully_received
    delivery.save(update_fields=["is_complete"])

    return delivery


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
            "item", "item__category", "item__location", "reviewed_by"
        )
        .prefetch_related("item__item_suppliers__supplier", "item__item_suppliers")
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

    def create(self, request, *args, **kwargs):
        """Create a new reorder request.

        Public endpoint (see :meth:`get_permissions`). For authenticated
        users, applies the membership-level permission gate so a SIG
        member can't reorder for another SIG's inventory. Anonymous
        callers go straight to the serializer — the serializer only
        exposes safe fields, so they cannot smuggle cost / supplier-URL
        data into the row.
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

        # Anonymous callers get the limited create-serializer shape back
        # so no admin metadata (admin_notes, invoice_url, supplier_url,
        # actual_cost, …) leaks even by accident. Authenticated callers
        # keep the existing richer response.
        instance = ReorderRequest.objects.get(id=serializer.instance.id)
        if user.is_authenticated:
            output_serializer = ReorderRequestSerializer(instance)
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
            pending = self.queryset.filter(status="pending").order_by("-priority", "requested_at")

            # Filter by SIG ownership for SIG admins
            user = request.user
            if user.is_authenticated and not (user.is_superuser or user.is_staff):
                from membership.utils import get_user_managed_sigs, is_logistics_member

                # Logistics can see everything
                if not is_logistics_member(user):
                    # SIG admins can only see requests for their SIG's inventory
                    user_sigs = get_user_managed_sigs(user)
                    if user_sigs.exists():
                        pending = pending.filter(item__owning_group__in=user_sigs)

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

        pending = self.queryset.filter(status="pending", item__owning_group__in=user_sigs).order_by(
            "-priority", "requested_at"
        )
        serializer = self.get_serializer(pending, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated])
    def by_supplier(self, request):
        """Group pending requests by supplier for easier bulk ordering."""
        pending = (
            ReorderRequest.objects.filter(status="pending")
            .select_related("item")
            .prefetch_related("item__item_suppliers__supplier")
        )

        # Group by supplier
        suppliers = {}
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

            suppliers[supplier_name]["requests"].append(ReorderRequestSerializer(req).data)
            suppliers[supplier_name]["item_count"] += 1
            if req.estimated_cost:
                suppliers[supplier_name]["total_estimated_cost"] += float(req.estimated_cost)

        return Response(list(suppliers.values()))

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def approve(self, request, pk=None):
        """Approve a reorder request."""
        reorder = self.get_object()
        reorder.status = "approved"
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
        reorder.status = "ordered"
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
        """Mark a request as received and update inventory."""
        reorder = self.get_object()
        reorder.status = "received"
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
        reorder.status = "cancelled"
        reorder.reviewed_by = request.user
        reorder.reviewed_at = timezone.now()
        reorder.admin_notes = request.data.get("admin_notes", reorder.admin_notes)
        reorder.save()

        serializer = self.get_serializer(reorder)
        return Response(serializer.data)

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated])
    def generate_cart_links(self, request):
        """Generate shopping cart links for approved items by supplier."""
        approved = (
            ReorderRequest.objects.filter(status="approved")
            .select_related("item")
            .prefetch_related("item__item_suppliers__supplier")
        )

        supplier_items = {}
        for req in approved:
            if not req.item.supplier:
                continue

            supplier_type = req.item.supplier.supplier_type
            if supplier_type not in supplier_items:
                supplier_items[supplier_type] = []

            supplier_items[supplier_type].append(
                {
                    "item_name": req.item.name,
                    "quantity": req.quantity,
                    "supplier_sku": req.item.supplier_sku,
                    "supplier_url": req.item.supplier_url,
                    "estimated_cost": (float(req.estimated_cost) if req.estimated_cost else None),
                }
            )

        # Generate cart links/data for each supplier
        cart_data = {}
        for supplier_type, items in supplier_items.items():
            if supplier_type == "amazon":
                # Amazon: Generate add-to-cart URLs
                cart_data[supplier_type] = {
                    "supplier": "Amazon",
                    "items": items,
                    "instructions": "Click the supplier URL for each item to add to cart manually",
                }
            elif supplier_type == "grainger":
                # Grainger: Similar approach
                cart_data[supplier_type] = {
                    "supplier": "Grainger",
                    "items": items,
                    "instructions": "Use supplier SKUs to build cart on Grainger website",
                }
            elif supplier_type == "hdsupply":
                # HD Supply
                cart_data[supplier_type] = {
                    "supplier": "HD Supply",
                    "items": items,
                    "instructions": "Use supplier SKUs to build cart on HD Supply website",
                }

        return Response(cart_data)


class PurchaseOrderViewSet(viewsets.ModelViewSet):
    """API endpoint for purchase order management."""

    queryset = PurchaseOrder.objects.select_related(
        "supplier", "created_by", "sent_by"
    ).prefetch_related(
        "items__item_supplier__item",
        "items__item_supplier__supplier",
        "items__asset",
        "items__asset__manufacturer",
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
                    PurchaseOrder.SENT,
                    PurchaseOrder.CONFIRMED,
                    PurchaseOrder.PARTIALLY_RECEIVED,
                    PurchaseOrder.RECEIVED,
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

        # Hide POs with no active line items from the list view (oms-a8o).
        # A PO whose items are all voided has nothing to show or pay for, so it
        # should not appear in the public/admin purchase order list. Detail
        # retrieval is unaffected so deep links and audit trails still resolve.
        if self.action == "list":
            queryset = queryset.annotate(
                _active_items_count=Count("items", filter=Q(items__is_voided=False))
            ).filter(_active_items_count__gt=0)

        return queryset

    def get_serializer_class(self):
        if self.action == "create":
            return PurchaseOrderCreateSerializer
        return PurchaseOrderSerializer

    def perform_create(self, serializer):
        purchase_order = serializer.save()
        record_audit_event(
            action=PurchaseOrderAuditEvent.ACTION_PO_CREATE,
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
        if purchase_order.status != PurchaseOrder.DRAFT:
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
        ``sent_by``/``sent_at`` stamped, a ``po_send`` audit event recorded, and
        the linked reorder requests synced. Callers own the DRAFT precondition.
        """
        purchase_order.status = PurchaseOrder.SENT
        purchase_order.sent_by = user
        purchase_order.sent_at = timezone.now()
        purchase_order.save()
        record_audit_event(
            action=PurchaseOrderAuditEvent.ACTION_PO_SEND,
            actor=user,
            purchase_order=purchase_order,
            metadata={"po_number": purchase_order.po_number},
        )
        # Keep linked reorder requests in step with the PO going out.
        self._update_reorder_requests_from_po(purchase_order)

    @action(detail=False, methods=["post"])
    def create_optimized_order(self, request):
        """Create an optimized purchase order based on current needs and supplier analysis."""

        # Get items that need reordering
        low_stock_items = (
            InventoryItem.objects.filter(current_stock__lte=F("minimum_stock"))
            .select_related("category", "location")
            .prefetch_related("item_suppliers__supplier")
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
                        "estimated_total": Decimal("0.00"),
                    }

                # Calculate optimal quantity (considering package sizes)
                optimal_qty = self._calculate_optimal_quantity(item, best_supplier)

                supplier_groups[supplier_id]["items"].append(
                    {
                        "item_id": item.id,
                        "item_name": item.name,
                        "item_supplier_id": best_supplier.id,
                        "current_stock": item.current_stock,
                        "minimum_stock": item.minimum_stock,
                        "recommended_quantity": optimal_qty,
                        "unit_cost": best_supplier.unit_cost,
                        "package_cost": best_supplier.package_cost,
                        "quantity_per_package": best_supplier.quantity_per_package,
                        "estimated_line_total": optimal_qty * (best_supplier.unit_cost or 0),
                    }
                )

                supplier_groups[supplier_id]["estimated_total"] += optimal_qty * (
                    best_supplier.unit_cost or 0
                )

        # Prepare recommendations for review
        for supplier_id, group in supplier_groups.items():
            recommendations.append(
                {
                    "supplier_id": supplier_id,
                    "supplier_name": group["supplier"].name,
                    "supplier_type": group["supplier"].supplier_type,
                    "total_items": len(group["items"]),
                    "estimated_total": group["estimated_total"],
                    "items": group["items"],
                }
            )

        # Sort by estimated total (largest orders first)
        recommendations.sort(key=lambda x: x["estimated_total"], reverse=True)

        return Response(
            {
                "recommendations": recommendations,
                "total_suppliers": len(recommendations),
                "total_estimated_cost": sum(r["estimated_total"] for r in recommendations),
                "message": "Order recommendations generated. Review and confirm to create purchase orders.",
            }
        )

    @action(detail=False, methods=["get"])
    def reorder_data(self, request):
        """
        Get data for PO creation: items with active reorder requests prioritized,
        then low stock items grouped by supplier with pricing.

        Returns all suppliers that have items needing reorder, along with
        assets where the supplier is the manufacturer.
        """
        from inventory.models import Asset, Supplier

        # First, get items with active reorder requests (pending or approved)
        items_with_requests = (
            InventoryItem.objects.filter(
                reorder_requests__status__in=[ReorderRequest.PENDING, ReorderRequest.APPROVED],
                is_active=True,
            )
            .distinct()
            .select_related("category", "location")
            .prefetch_related("item_suppliers__supplier", "reorder_requests")
        )

        # Also get items that need reordering (current_stock <= minimum_stock)
        # but exclude those already in items_with_requests
        low_stock_items = (
            InventoryItem.objects.filter(
                current_stock__lte=F("minimum_stock"),
                is_active=True,
            )
            .exclude(id__in=items_with_requests.values_list("id", flat=True))
            .select_related("category", "location")
            .prefetch_related("item_suppliers__supplier")
        )

        # Combine both sets, prioritizing items with requests
        all_items = list(items_with_requests) + list(low_stock_items)

        # Build supplier data with their available items
        supplier_data = {}

        for item in all_items:
            # Get all active, non-discontinued suppliers for this item
            item_suppliers = item.item_suppliers.filter(
                is_active=True,
                is_discontinued=False,
            ).select_related("supplier")

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
                        "total_items": 0,
                        "estimated_total": Decimal("0.00"),
                        "avg_lead_time": 0,
                    }

                # Calculate suggested quantity
                # If item has an active reorder request, use that quantity
                active_request = item.get_active_reorder_request()
                if active_request:
                    suggested_qty = active_request.quantity
                else:
                    shortage = max(0, item.minimum_stock - item.current_stock)
                    suggested_qty = max(shortage, item.reorder_quantity)

                # Adjust for package quantities
                if item_supplier.quantity_per_package and item_supplier.quantity_per_package > 1:
                    packages_needed = (
                        suggested_qty + item_supplier.quantity_per_package - 1
                    ) // item_supplier.quantity_per_package
                    suggested_qty = packages_needed * item_supplier.quantity_per_package

                unit_cost = item_supplier.unit_cost or Decimal("0.00")
                line_total = unit_cost * suggested_qty

                # Check if item has an active reorder request
                has_active_request = active_request is not None
                reorder_request_id = active_request.id if active_request else None

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
                        "unit_cost": str(unit_cost),
                        "package_cost": (
                            str(item_supplier.package_cost) if item_supplier.package_cost else None
                        ),
                        "quantity_per_package": item_supplier.quantity_per_package,
                        "lead_time_days": item_supplier.average_lead_time,
                        "supplier_sku": item_supplier.supplier_sku,
                        "supplier_url": item_supplier.supplier_url,
                        "is_primary": item_supplier.is_primary,
                        "line_total": str(line_total),
                        "has_active_reorder_request": has_active_request,
                        "reorder_request_id": reorder_request_id,
                    }
                )

                supplier_data[supplier_id]["total_items"] += 1
                supplier_data[supplier_id]["estimated_total"] += line_total

        # Add assets for each supplier (where supplier is the manufacturer)
        for supplier_id, data in supplier_data.items():
            assets = Asset.objects.filter(
                manufacturer_id=supplier_id,
                is_active=True,
                status__in=[Asset.ACTIVE, Asset.MAINTENANCE, Asset.IMPLEMENTING, Asset.TESTING],
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
            data["estimated_total"] = str(data["estimated_total"])

        # Also include suppliers that have assets but no low-stock items
        suppliers_with_assets = (
            Supplier.objects.filter(
                manufactured_assets__is_active=True,
                manufactured_assets__status__in=[
                    Asset.ACTIVE,
                    Asset.MAINTENANCE,
                    Asset.IMPLEMENTING,
                    Asset.TESTING,
                ],
            )
            .exclude(id__in=supplier_data.keys())
            .distinct()
        )

        for supplier in suppliers_with_assets:
            assets = Asset.objects.filter(
                manufacturer=supplier,
                is_active=True,
                status__in=[Asset.ACTIVE, Asset.MAINTENANCE, Asset.IMPLEMENTING, Asset.TESTING],
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
                "total_items": 0,
                "estimated_total": "0.00",
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
            }
        )

    def _find_best_supplier(self, item):
        """Find the best supplier for an item based on cost, availability, and lead time."""
        suppliers = item.item_suppliers.filter(is_active=True, is_discontinued=False)

        if not suppliers.exists():
            return None

        # Score each supplier
        scored_suppliers = []
        for supplier in suppliers:
            score = 0

            # Cost factor (40% weight) - lower cost is better
            if supplier.unit_cost:
                # Normalize cost score (assuming max reasonable cost difference of 50%)
                cost_factor = (
                    max(
                        0,
                        50
                        - (
                            (
                                supplier.unit_cost
                                / suppliers.aggregate(avg_cost=Avg("unit_cost"))["avg_cost"]
                                - 1
                            )
                            * 100
                        ),
                    )
                    / 50
                )
                score += cost_factor * 0.4

            # Lead time factor (30% weight) - shorter lead time is better
            if supplier.average_lead_time:
                # Normalize lead time (assuming max reasonable lead time of 30 days)
                lead_time_factor = max(0, (30 - supplier.average_lead_time) / 30)
                score += lead_time_factor * 0.3

            # Primary supplier bonus (20% weight)
            if supplier.is_primary:
                score += 0.2

            # Historical performance bonus (10% weight)
            # TODO: Implement based on LeadTimeLog data
            performance_factor = 0.1  # Default neutral performance
            score += performance_factor * 0.1

            scored_suppliers.append((supplier, score))

        # Return the highest scoring supplier
        scored_suppliers.sort(key=lambda x: x[1], reverse=True)
        return scored_suppliers[0][0] if scored_suppliers else None

    def _calculate_optimal_quantity(self, item, supplier):
        """Calculate optimal order quantity considering package sizes and stock needs."""
        # Calculate basic reorder quantity
        shortage = max(0, item.minimum_stock - item.current_stock)
        base_quantity = max(shortage, item.reorder_quantity)

        # Adjust for package quantities if available
        if supplier.quantity_per_package and supplier.quantity_per_package > 1:
            # Round up to nearest package
            packages_needed = (
                base_quantity + supplier.quantity_per_package - 1
            ) // supplier.quantity_per_package
            return packages_needed * supplier.quantity_per_package

        return base_quantity

    def _update_reorder_requests_from_po(self, purchase_order):
        """
        Update associated ReorderRequest objects when a PurchaseOrder is finalized.

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
                status__in=[ReorderRequest.PENDING, ReorderRequest.APPROVED],
            )

            # Calculate estimated delivery date
            estimated_delivery = None
            if purchase_order.expected_delivery_date:
                estimated_delivery = purchase_order.expected_delivery_date
            elif po_item.item_supplier.average_lead_time:
                # Calculate from lead time in business days
                order_date = (
                    purchase_order.sent_at.date()
                    if purchase_order.sent_at
                    else timezone.now().date()
                )
                lead_time_days = po_item.item_supplier.average_lead_time
                estimated_delivery = self._add_business_days(order_date, lead_time_days)

            # Update each active request
            for reorder_request in active_requests:
                reorder_request.status = ReorderRequest.ORDERED
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

    def _add_business_days(self, start_date, business_days):
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

    @action(detail=True, methods=["post"])
    def send_to_supplier(self, request, pk=None):
        """Mark purchase order as sent to supplier."""
        purchase_order = self.get_object()

        if purchase_order.status != PurchaseOrder.DRAFT:
            return Response(
                {"error": "Only draft orders can be sent to suppliers"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        self._mark_sent(purchase_order, request.user)

        serializer = self.get_serializer(purchase_order)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def confirm_order(self, request, pk=None):
        """Mark purchase order as confirmed by supplier."""
        purchase_order = self.get_object()

        if purchase_order.status != PurchaseOrder.SENT:
            return Response(
                {"error": "Only sent orders can be confirmed"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        purchase_order.status = PurchaseOrder.CONFIRMED
        purchase_order.expected_delivery_date = request.data.get("expected_delivery_date")
        purchase_order.save()

        serializer = self.get_serializer(purchase_order)
        return Response(serializer.data)

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

        if purchase_order.status not in [
            PurchaseOrder.SENT,
            PurchaseOrder.CONFIRMED,
            PurchaseOrder.PARTIALLY_RECEIVED,
        ]:
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

        pending_items = [item for item in purchase_order.items.all() if not item.is_fully_received]
        if not pending_items:
            return Response(
                {"error": "All items in this purchase order are already fully received"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        delivery_datetime = timezone.make_aware(
            datetime.combine(data["delivery_date"], datetime.min.time())
        )

        with transaction.atomic():
            _apply_po_receipt(
                purchase_order,
                [(po_item, po_item.quantity_pending) for po_item in pending_items],
                received_by=request.user,
                delivery_datetime=delivery_datetime,
                tracking_number=data.get("tracking_number", ""),
                carrier=data.get("carrier", ""),
                receipt_notes=data.get("receipt_notes", ""),
            )

        record_audit_event(
            action="po_mark_delivered",
            actor=request.user,
            purchase_order=purchase_order,
            notes=data.get("receipt_notes", ""),
            metadata={
                "delivery_date": delivery_datetime.isoformat(),
                "tracking_number": data.get("tracking_number", ""),
                "carrier": data.get("carrier", ""),
                "fully_received": purchase_order.status == PurchaseOrder.RECEIVED,
            },
        )

        response_serializer = self.get_serializer(purchase_order)
        return Response(response_serializer.data)

    @action(detail=True, methods=["post"], url_path="receive")
    def receive(self, request, pk=None):
        """Receive specific line items with explicit per-item quantities.

        Where ``mark_delivered`` receives every pending quantity on the whole
        PO, this records a partial receipt of exactly the quantities supplied
        per line — the flow Scantty and the web UI use to receive a delivery
        that contains only some of what was ordered. Shares the receipt side
        effects (delivery, stock, status, lead-time logs) with
        ``mark_delivered`` via :func:`_apply_po_receipt`.
        """
        from datetime import datetime

        purchase_order = self.get_object()

        if purchase_order.status not in [
            PurchaseOrder.SENT,
            PurchaseOrder.CONFIRMED,
            PurchaseOrder.PARTIALLY_RECEIVED,
        ]:
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
        remaining_by_item = {}
        resolved_lines = []
        for line in data["items"]:
            po_item_id = line["purchase_order_item"]
            quantity = line["quantity_received"]

            po_item = po_items_by_id.get(po_item_id)
            if po_item is None:
                return Response(
                    {"error": f"Line item {po_item_id} does not belong to this purchase order"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if po_item.is_voided:
                return Response(
                    {"error": f"Line item {po_item_id} is voided and cannot be received"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Track remaining allowance per line so repeated references to the
            # same line in one request can't collectively over-receive.
            if po_item_id not in remaining_by_item:
                remaining_by_item[po_item_id] = po_item.quantity_pending
            if quantity > remaining_by_item[po_item_id]:
                return Response(
                    {
                        "error": (
                            f"Quantity {quantity} exceeds pending "
                            f"{remaining_by_item[po_item_id]} for line item {po_item_id}"
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            remaining_by_item[po_item_id] -= quantity
            resolved_lines.append((po_item, quantity))

        delivery_date = data.get("delivery_date")
        if delivery_date is not None:
            delivery_datetime = timezone.make_aware(
                datetime.combine(delivery_date, datetime.min.time())
            )
        else:
            delivery_datetime = timezone.now()

        with transaction.atomic():
            _apply_po_receipt(
                purchase_order,
                resolved_lines,
                received_by=request.user,
                delivery_datetime=delivery_datetime,
                tracking_number=data.get("tracking_number", ""),
                carrier=data.get("carrier", ""),
                receipt_notes=data.get("receipt_notes", ""),
            )

        record_audit_event(
            action="po_receive_items",
            actor=request.user,
            purchase_order=purchase_order,
            notes=data.get("receipt_notes", ""),
            metadata={
                "delivery_date": delivery_datetime.isoformat(),
                "tracking_number": data.get("tracking_number", ""),
                "carrier": data.get("carrier", ""),
                "fully_received": purchase_order.status == PurchaseOrder.RECEIVED,
                "received_items": [
                    {"purchase_order_item": po_item.id, "quantity_received": quantity}
                    for po_item, quantity in resolved_lines
                ],
            },
        )

        response_serializer = self.get_serializer(purchase_order)
        return Response(response_serializer.data)

    @action(detail=True, methods=["patch"], url_path="items/(?P<item_id>[^/.]+)")
    def update_item(self, request, pk=None, item_id=None):
        """Update a specific line item in a purchase order."""
        purchase_order = self.get_object()
        try:
            line_item = PurchaseOrderItem.objects.get(id=item_id, purchase_order=purchase_order)
        except PurchaseOrderItem.DoesNotExist:
            return Response({"error": "Line item not found"}, status=status.HTTP_404_NOT_FOUND)

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

        line_item.save()

        from .serializers import PurchaseOrderItemSerializer

        serializer = PurchaseOrderItemSerializer(line_item)
        return Response(serializer.data)

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

        # Void the line item
        line_item.is_voided = True
        line_item.voided_at = timezone.now()
        line_item.voided_by = request.user
        line_item.void_reason = request.data.get("reason", "Item discontinued by supplier")

        # If this is an item_supplier relationship, mark it as discontinued
        if line_item.item_supplier:
            line_item.item_supplier.is_discontinued = True
            line_item.item_supplier.is_active = False
            line_item.item_supplier.save()

        line_item.save()

        record_audit_event(
            action="po_line_void",
            actor=request.user,
            line_item=line_item,
            notes=line_item.void_reason or "",
        )

        from .serializers import PurchaseOrderItemSerializer

        serializer = PurchaseOrderItemSerializer(line_item)
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

        if purchase_order.status == PurchaseOrder.VOIDED:
            return Response(
                {"detail": "Purchase order is already voided."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if purchase_order.status == PurchaseOrder.RECEIVED:
            return Response(
                {"detail": "Cannot void a received PO; create a return instead."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        now = timezone.now()
        reason = request.data.get("reason", "")

        with transaction.atomic():
            purchase_order.status = PurchaseOrder.VOIDED
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

        record_audit_event(
            action="po_void",
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
            action="attachment_add",
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
            action="attachment_remove",
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
            draft=Count("id", filter=Q(status=PurchaseOrder.DRAFT)),
            sent=Count("id", filter=Q(status=PurchaseOrder.SENT)),
            confirmed=Count("id", filter=Q(status=PurchaseOrder.CONFIRMED)),
            partially_received=Count("id", filter=Q(status=PurchaseOrder.PARTIALLY_RECEIVED)),
            received=Count("id", filter=Q(status=PurchaseOrder.RECEIVED)),
        )

        # Financial metrics
        financial_metrics = PurchaseOrder.objects.aggregate(
            total_value=Sum("estimated_total"),
            received_value=Sum("actual_total", filter=Q(status=PurchaseOrder.RECEIVED)),
        )

        # Recent activity (this week)
        week_ago = timezone.now() - timedelta(days=7)
        recent_activity = PurchaseOrder.objects.filter(order_date__gte=week_ago).aggregate(
            orders_created=Count("id"),
            orders_received=Count("id", filter=Q(status=PurchaseOrder.RECEIVED)),
        )

        # Items metrics
        item_metrics = PurchaseOrderItem.objects.aggregate(
            total_items_ordered=Sum("quantity_ordered"),
            total_items_received=Sum("quantity_received"),
        )

        # Calculate pending values
        pending_value = (financial_metrics["total_value"] or 0) - (
            financial_metrics["received_value"] or 0
        )
        items_pending = (item_metrics["total_items_ordered"] or 0) - (
            item_metrics["total_items_received"] or 0
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
                "total_items_on_order": item_metrics["total_items_ordered"] or 0,
                "total_items_received": item_metrics["total_items_received"] or 0,
                "items_pending_receipt": items_pending,
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
        "purchase_order__supplier", "received_by"
    ).prefetch_related("items__purchase_order_item__item_supplier__item")

    serializer_class = OrderDeliverySerializer
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["post"])
    def scan_barcode(self, request):
        """Process barcode scan for order receipt."""
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

        # Check if we can receive this quantity
        remaining_quantity = po_item.quantity_pending
        if quantity_received > remaining_quantity:
            return Response(
                {
                    "error": f"Cannot receive {quantity_received} items. Only {remaining_quantity} remaining to receive.",
                    "quantity_ordered": po_item.quantity_ordered,
                    "quantity_already_received": po_item.quantity_received,
                    "quantity_remaining": remaining_quantity,
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

            # Update purchase order status
            if purchase_order.is_fully_received:
                purchase_order.status = PurchaseOrder.RECEIVED
            else:
                purchase_order.status = PurchaseOrder.PARTIALLY_RECEIVED
            purchase_order.save()

            # Create lead time log if order is complete
            if po_item.is_fully_received:
                self._create_lead_time_log(po_item, delivery.delivery_date)

        return Response(
            {
                "success": True,
                "message": f"Successfully received {quantity_received} units of {po_item.item.name}",
                "item_name": po_item.item.name,
                "quantity_received": quantity_received,
                "total_received": po_item.quantity_received,
                "quantity_remaining": po_item.quantity_pending,
                "order_status": purchase_order.status,
                "updated_inventory_stock": item.current_stock,
            }
        )

    def _create_lead_time_log(self, po_item, delivery_date):
        """Create a lead time log entry when an item is fully received."""
        _create_lead_time_log(po_item, delivery_date)

    @action(detail=False, methods=["get"])
    def pending_orders(self, request):
        """Get all orders that are expecting deliveries."""
        pending_orders = (
            PurchaseOrder.objects.filter(
                status__in=[
                    PurchaseOrder.SENT,
                    PurchaseOrder.CONFIRMED,
                    PurchaseOrder.PARTIALLY_RECEIVED,
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
                    "items_pending": order.total_quantity - order.total_received_quantity,
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
            .prefetch_related("item__item_suppliers__supplier")
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
            completed_orders = orders.filter(status=PurchaseOrder.RECEIVED).count()
            active_orders = orders.exclude(
                status__in=[PurchaseOrder.RECEIVED, PurchaseOrder.CANCELLED]
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
                        float(order.estimated_cost) if order.estimated_cost else None
                    ),
                    "actual_cost": (float(order.actual_cost) if order.actual_cost else None),
                    "cost_per_unit": (float(order.cost_per_unit) if order.cost_per_unit else None),
                    "cost_variance": (
                        float(order.actual_cost - order.estimated_cost)
                        if (order.actual_cost and order.estimated_cost)
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
                            float(order.estimated_cost) if order.estimated_cost else None
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
                        PurchaseOrder.SENT,
                        PurchaseOrder.CONFIRMED,
                        PurchaseOrder.PARTIALLY_RECEIVED,
                        PurchaseOrder.RECEIVED,
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
                    "estimated_total": (float(po.estimated_total) if po.estimated_total else None),
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
            status__in=[LocationProblem.REPORTED, LocationProblem.IN_PROGRESS]
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
                LocationProblem.SEVERITY_HIGH,
                LocationProblem.SEVERITY_URGENT,
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
            action=WebhookAuditEvent.ACTION_WEBHOOK_CREATE,
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
                action=WebhookAuditEvent.ACTION_WEBHOOK_SECRET_ROTATE,
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
                action=WebhookAuditEvent.ACTION_WEBHOOK_UPDATE,
                actor=self.request.user,
                webhook=webhook,
                metadata={"changes": changes},
            )
        if before_active != webhook.is_active:
            record_webhook_audit_event(
                action=(
                    WebhookAuditEvent.ACTION_WEBHOOK_ENABLE
                    if webhook.is_active
                    else WebhookAuditEvent.ACTION_WEBHOOK_DISABLE
                ),
                actor=self.request.user,
                webhook=webhook,
            )

    def perform_destroy(self, instance):
        # Record audit BEFORE delete so the metadata captures identifying
        # fields while we still have the row. The webhook FK on the audit
        # row is SET_NULL on cascade, so the audit row survives the delete.
        record_webhook_audit_event(
            action=WebhookAuditEvent.ACTION_WEBHOOK_DELETE,
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


class PurchasingReportViewSet(viewsets.ViewSet):
    """API endpoint for purchasing reports."""

    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["get"])
    def spend_by_supplier(self, request):
        """Get total spend per supplier from purchase orders."""
        queryset = (
            PurchaseOrder.objects.filter(status=PurchaseOrder.RECEIVED)
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
                purchase_order__status=PurchaseOrder.RECEIVED,
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
                if (
                    first_price
                    and latest_price
                    and first_price.unit_cost
                    and latest_price.unit_cost
                ):
                    change = (
                        (latest_price.unit_cost - first_price.unit_cost) / first_price.unit_cost
                    ) * 100
                    price_change_pct = round(change, 2)

            data.append(
                {
                    "item_id": str(item["item_supplier__item__id"]),
                    "item_name": item["item_supplier__item__name"],
                    "supplier_name": item["item_supplier__supplier__name"],
                    "price_changes": item["price_changes"],
                    "min_unit_cost": float(item["min_unit_cost"] or 0),
                    "max_unit_cost": float(item["max_unit_cost"] or 0),
                    "latest_unit_cost": float(latest_price.unit_cost or 0) if latest_price else 0,
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
                        "min_unit_cost": f"{row['min_unit_cost']:.2f}",
                        "max_unit_cost": f"{row['max_unit_cost']:.2f}",
                        "latest_unit_cost": f"{row['latest_unit_cost']:.2f}",
                        "price_change_percentage": (
                            f"{row['price_change_percentage']:.2f}%"
                            if row["price_change_percentage"]
                            else ""
                        ),
                    }
                )

            return response_obj

        return Response({"error": "Invalid report type"}, status=status.HTTP_400_BAD_REQUEST)
