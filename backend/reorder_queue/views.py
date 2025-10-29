"""
Views for reorder queue API.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from django.db import transaction, models
from django.db.models import Count, Sum, Avg, Q, F
from django.utils import timezone

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from inventory.models import ItemSupplier, InventoryItem

from .models import (
    ReorderRequest,
    PurchaseOrder,
    PurchaseOrderItem,
    OrderDelivery,
    DeliveryItem,
    LeadTimeLog,
)
from .serializers import (
    ReorderRequestCreateSerializer,
    ReorderRequestSerializer,
    PurchaseOrderSerializer,
    PurchaseOrderCreateSerializer,
    OrderDeliverySerializer,
    BarcodeReceiptSerializer,
    LeadTimeLogSerializer,
    OrderMetricsSerializer,
    SupplierPerformanceSerializer,
)


class ReorderRequestViewSet(viewsets.ModelViewSet):
    """API endpoint for reorder requests."""

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
                    "estimated_cost": float(req.estimated_cost) if req.estimated_cost else None,
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
        "items__item_supplier__item", "items__item_supplier__supplier", "deliveries__items"
    )
    permission_classes = [IsAuthenticated]

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
                {"message": "No items currently need reordering"}, status=status.HTTP_200_OK
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
        suppliers = item.item_suppliers.filter(is_active=True)

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
                {"error": "Only sent orders can be confirmed"}, status=status.HTTP_400_BAD_REQUEST
            )

        purchase_order.status = PurchaseOrder.CONFIRMED
        purchase_order.expected_delivery_date = request.data.get("expected_delivery_date")
        purchase_order.save()

        serializer = self.get_serializer(purchase_order)
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
            delivery_item = DeliveryItem.objects.create(
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
