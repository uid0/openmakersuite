"""
Views for reorder queue API.
"""

from datetime import timedelta
from decimal import Decimal

from django.db import models, transaction
from django.db.models import Avg, Count, F, Q, Sum
from django.utils import timezone

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from inventory.models import InventoryItem

from .models import (
    DeliveryItem,
    LeadTimeLog,
    OrderDelivery,
    PurchaseOrder,
    PurchaseOrderItem,
    ReorderRequest,
    WebHook,
)
from .serializers import (
    BarcodeReceiptSerializer,
    OrderDeliverySerializer,
    OrderMetricsSerializer,
    PurchaseOrderCreateSerializer,
    PurchaseOrderSerializer,
    ReorderRequestCreateSerializer,
    ReorderRequestSerializer,
    SupplierPerformanceSerializer,
    WebHookCreateSerializer,
    WebHookSerializer,
    WebHookTestResultSerializer,
)


class ReorderRequestViewSet(viewsets.ModelViewSet):
    """
    API endpoint for reorder requests.

    Public endpoint - allows unauthenticated QR code scanning for creating requests.
    Admin actions require JWT authentication.
    """

    authentication_classes = (JWTAuthentication,)  # Only JWT, no session auth needed
    queryset = (
        ReorderRequest.objects.select_related("item", "reviewed_by")
        .prefetch_related("item__item_suppliers__supplier")
        .all()
    )

    def get_serializer_class(self):
        if self.action == "create":
            return ReorderRequestCreateSerializer
        return ReorderRequestSerializer

    def get_permissions(self):
        """Allow anyone to create reorder requests and view pending, but require auth for admin actions."""
        # Public actions that don't require authentication
        if self.action in ["create", "list", "retrieve", "pending"]:
            return [AllowAny()]
        # Admin actions require authentication
        return [IsAuthenticated()]

    def create(self, request, *args, **kwargs):
        """Create a new reorder request."""
        # Check permissions if user is authenticated
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

        # Return full details
        instance = ReorderRequest.objects.get(id=serializer.instance.id)
        output_serializer = ReorderRequestSerializer(instance)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    @action(detail=False, methods=["get"])
    def pending(self, request):
        """Get all pending reorder requests."""
        # Return all pending requests without pagination for admin dashboard
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
        """Mark a request as ordered."""
        reorder = self.get_object()
        reorder.status = "ordered"
        reorder.ordered_at = timezone.now()
        reorder.order_number = request.data.get("order_number", "")
        reorder.estimated_delivery = request.data.get("estimated_delivery")
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

        return queryset

    def get_serializer_class(self):
        if self.action == "create":
            return PurchaseOrderCreateSerializer
        return PurchaseOrderSerializer

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

    @action(detail=True, methods=["post"])
    def send_to_supplier(self, request, pk=None):
        """Mark purchase order as sent to supplier."""
        purchase_order = self.get_object()

        if purchase_order.status != PurchaseOrder.DRAFT:
            return Response(
                {"error": "Only draft orders can be sent to suppliers"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        purchase_order.status = PurchaseOrder.SENT
        purchase_order.sent_by = request.user
        purchase_order.sent_at = timezone.now()
        purchase_order.save()

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

        from .serializers import PurchaseOrderItemSerializer

        serializer = PurchaseOrderItemSerializer(line_item)
        return Response(serializer.data)

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
        purchase_order = po_item.purchase_order

        if not purchase_order.sent_at:
            return  # Can't calculate lead time without send date

        # Calculate business days
        order_date = purchase_order.sent_at
        actual_delivery_date = (
            delivery_date.date() if hasattr(delivery_date, "date") else delivery_date
        )

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

    @action(detail=False, methods=["get"], permission_classes=[AllowAny])
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

    @action(detail=False, methods=["get"], permission_classes=[AllowAny])
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

        from inventory.models import Asset, AssetPart
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

        # Combine all unique location IDs
        all_problem_location_ids = set(
            list(locations_with_tasks)
            + list(locations_with_security)
            + list(locations_with_feedback)
        )
        open_locations_with_problems = len(all_problem_location_ids)

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
                "assets_overdue_maintenance": overdue_maintenance_count,
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

    @action(detail=True, methods=["post"], url_path="test", url_name="test")
    def test(self, request, pk=None):
        """
        Test a webhook by sending a test payload.

        This sends a test webhook notification immediately and returns the result.
        """
        import hashlib
        import hmac
        import json
        import time

        import requests

        webhook = self.get_object()

        # Prepare test payload
        test_payload = {
            "event": webhook.event_type,
            "test": True,
            "timestamp": timezone.now().isoformat(),
            "data": {
                "message": "This is a test webhook notification",
                "webhook_id": webhook.id,
                "webhook_name": webhook.name,
            },
        }

        start_time = time.time()

        try:
            # Prepare request
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "DMS-Inventory-Webhook/1.0",
            }

            # Add custom headers
            if webhook.headers:
                headers.update(webhook.headers)

            # Prepare payload
            json_payload = json.dumps(test_payload)

            # Add HMAC signature if configured
            if webhook.secret:
                signature = hmac.new(
                    webhook.secret.encode("utf-8"),
                    json_payload.encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest()
                headers["X-Webhook-Signature"] = f"sha256={signature}"

            # Send test webhook
            response = requests.post(webhook.url, data=json_payload, headers=headers, timeout=30)

            response_time_ms = (time.time() - start_time) * 1000

            response.raise_for_status()

            # Record success (but don't update statistics for test)
            result = {
                "webhook_id": webhook.id,
                "webhook_name": webhook.name,
                "success": True,
                "status_code": response.status_code,
                "response_time_ms": round(response_time_ms, 2),
                "response_body": response.text[:500],  # First 500 chars
                "tested_at": timezone.now(),
            }

            serializer = WebHookTestResultSerializer(result)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except requests.exceptions.RequestException as e:
            response_time_ms = (time.time() - start_time) * 1000
            result = {
                "webhook_id": webhook.id,
                "webhook_name": webhook.name,
                "success": False,
                "status_code": None,
                "response_time_ms": round(response_time_ms, 2),
                "error_message": str(e),
                "tested_at": timezone.now(),
            }

            serializer = WebHookTestResultSerializer(result)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except Exception as e:
            response_time_ms = (time.time() - start_time) * 1000
            result = {
                "webhook_id": webhook.id,
                "webhook_name": webhook.name,
                "success": False,
                "status_code": None,
                "response_time_ms": round(response_time_ms, 2),
                "error_message": f"Unexpected error: {str(e)}",
                "tested_at": timezone.now(),
            }

            serializer = WebHookTestResultSerializer(result)
            return Response(serializer.data, status=status.HTTP_200_OK)
