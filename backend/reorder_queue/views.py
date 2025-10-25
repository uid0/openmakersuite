"""
Views for reorder queue API.
"""

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticatedOrReadOnly, IsAuthenticated
from django.utils import timezone
from django.db.models import Q, Sum, Count

from .models import ReorderRequest
from .serializers import ReorderRequestSerializer, ReorderRequestCreateSerializer
from inventory.models import InventoryItem


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
        """Allow anyone to create reorder requests, but require auth for updates."""
        if self.action == "create":
            return []
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
        pending = self.queryset.filter(status="pending").order_by("-priority", "requested_at")
        page = self.paginate_queryset(pending)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
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
