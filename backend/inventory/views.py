"""
Views for inventory API.
"""

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticatedOrReadOnly, IsAuthenticated
from django.http import HttpResponse
from django.shortcuts import get_object_or_404

from .models import Supplier, Category, InventoryItem, UsageLog
from .serializers import (
    SupplierSerializer,
    CategorySerializer,
    InventoryItemSerializer,
    InventoryItemDetailSerializer,
    UsageLogSerializer,
)
from .tasks import generate_qr_code, generate_index_card


class SupplierViewSet(viewsets.ModelViewSet):
    """API endpoint for suppliers."""

    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]


class CategoryViewSet(viewsets.ModelViewSet):
    """API endpoint for categories."""

    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [IsAuthenticatedOrReadOnly]


class InventoryItemViewSet(viewsets.ModelViewSet):
    """API endpoint for inventory items."""

    queryset = InventoryItem.objects.select_related("supplier", "category").all()
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return InventoryItemDetailSerializer
        return InventoryItemSerializer

    @action(detail=True, methods=["post"])
    def generate_qr(self, request, pk=None):
        """Generate QR code for an item."""
        item = self.get_object()
        generate_qr_code.delay(str(item.id))
        return Response({"status": "QR code generation started"})

    @action(detail=True, methods=["get"])
    def download_card(self, request, pk=None):
        """Generate and download 3x5 index card PDF."""
        item = self.get_object()

        # Generate PDF synchronously for immediate download
        from .utils.pdf_generator import generate_item_card

        pdf_buffer = generate_item_card(item)

        response = HttpResponse(pdf_buffer.getvalue(), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="card_{item.sku or item.id}.pdf"'
        return response

    @action(detail=False, methods=["get"])
    def low_stock(self, request):
        """Get items that need reordering."""
        low_stock_items = [item for item in self.queryset if item.needs_reorder]
        serializer = self.get_serializer(low_stock_items, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def log_usage(self, request, pk=None):
        """Log usage of an item."""
        item = self.get_object()
        quantity = request.data.get("quantity", 1)
        notes = request.data.get("notes", "")

        # Create usage log
        usage_log = UsageLog.objects.create(item=item, quantity_used=quantity, notes=notes)

        # Update stock
        if item.current_stock >= quantity:
            item.current_stock -= quantity
            item.save()

        return Response(UsageLogSerializer(usage_log).data)


class UsageLogViewSet(viewsets.ModelViewSet):
    """API endpoint for usage logs."""

    queryset = UsageLog.objects.select_related("item").all()
    serializer_class = UsageLogSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        item_id = self.request.query_params.get("item_id")
        if item_id:
            queryset = queryset.filter(item_id=item_id)
        return queryset
