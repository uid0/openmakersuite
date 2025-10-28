"""
Views for inventory API.
"""

from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.http import HttpResponse

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.response import Response

from .models import Category, InventoryItem, ItemSupplier, Location, PriceHistory, Supplier, UsageLog
from .serializers import (
    CategorySerializer,
    InventoryItemDetailSerializer,
    InventoryItemSerializer,
    ItemSupplierSerializer,
    PriceHistorySerializer,
    SupplierSerializer,
    UsageLogSerializer,
)
from .tasks import generate_qr_code


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

    queryset = (
        InventoryItem.objects.select_related("category", "location")
        .prefetch_related("item_suppliers__supplier")
        .all()
    )
    
    def get_permissions(self):
        """Allow public access for reading and common actions, require auth for admin operations."""
        # Public/common actions
        if self.action in ["list", "retrieve", "low_stock", "download_card", "log_usage", "generate_qr", "qr_code"]:
            return [AllowAny()]
        # Admin actions (create, update, delete)
        return [IsAuthenticated()]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return InventoryItemDetailSerializer
        return InventoryItemSerializer

    def get_queryset(self):
        return (
            InventoryItem.objects.select_related("category", "location")
            .prefetch_related("item_suppliers__supplier")
            .all()
        )

    def create(self, request, *args, **kwargs):
        data = request.data.copy()
        location = self._resolve_location(data.get("location"))
        if location:
            data["location"] = str(location.pk)
        elif "location" in data:
            data["location"] = None

        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            item = serializer.save()
            if location and item.location_id != location.pk:
                item.location = location
                item.save(update_fields=["location"])
            self._sync_primary_supplier(item, request.data)

        headers = self.get_success_headers(serializer.data)
        output_serializer = self.get_serializer(item)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    @action(detail=True, methods=["post"])
    def generate_qr(self, request, pk=None):
        """Generate QR code for an item."""
        item = self.get_object()

        # Generate QR code synchronously for immediate response
        from .utils.qr_generator import save_qr_code_to_item

        try:
            save_qr_code_to_item(item)
            return Response({
                "status": "QR code generated successfully",
                "qr_code_url": request.build_absolute_uri(item.qr_code.url) if item.qr_code else None
            })
        except Exception as e:
            return Response({"error": str(e)}, status=500)

    @action(
        detail=True,
        methods=["get"],
        url_path="qr_code",
        url_name="qr_code",
        name="QR Code"
    )
    def qr_code(self, request, pk=None):
        """Get QR code image for an item."""
        item = self.get_object()

        if not item.qr_code:
            return Response({"error": "QR code not generated yet"}, status=404)

        from django.http import HttpResponse
        response = HttpResponse(item.qr_code.read(), content_type="image/png")
        response["Content-Disposition"] = f'inline; filename="qr_{item.sku or item.id}.png"'
        return response

    @action(
        detail=True,
        methods=["get"],
        url_path="download_card",
        url_name="download_card",
        name="Download Card"
    )
    def download_card(self, request, pk=None):
        """Generate and download Avery 5388 compatible index card PDF."""
        item = self.get_object()

        # Check if blank card is requested
        blank_card = request.GET.get('blank', 'false').lower() == 'true'

        # Generate PDF using the index cards system
        from index_cards.services import IndexCardRenderer

        renderer = IndexCardRenderer(blank_cards=blank_card)
        pdf_bytes = renderer.render_preview(item, blank_card=blank_card)

        card_type = "blank" if blank_card else "detailed"
        filename = f"card_{item.sku or item.id}_{card_type}.pdf"

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    @action(
        detail=True,
        methods=["get"],
        url_path="download_blank_card",
        url_name="download_blank_card",
        name="Download Blank Card"
    )
    def download_blank_card(self, request, pk=None):
        """Generate and download blank card with only QR code for creative customization."""
        item = self.get_object()

        # Generate blank card PDF using the index cards system
        from index_cards.services import IndexCardRenderer

        renderer = IndexCardRenderer(blank_cards=True)
        pdf_bytes = renderer.render_preview(item, blank_card=True)

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="blank_card_{item.sku or item.id}.pdf"'
        return response

    @action(detail=False, methods=["get"])
    def low_stock(self, request):
        """Get items that need reordering."""
        low_stock_items = [
            item for item in self.filter_queryset(self.get_queryset()) if item.needs_reorder
        ]
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

    def _resolve_location(self, value):
        if not value:
            return None

        if isinstance(value, Location):
            return value

        try:
            return Location.objects.get(pk=value)
        except (Location.DoesNotExist, ValueError, TypeError):
            return Location.objects.get_or_create(name=str(value))[0]

    def _sync_primary_supplier(self, item, data):
        supplier_id = data.get("supplier")
        if not supplier_id:
            return

        try:
            supplier = Supplier.objects.get(pk=supplier_id)
        except Supplier.DoesNotExist:
            return

        # Handle both unit_cost (legacy) and package_cost (preferred)
        unit_cost = data.get("unit_cost")
        package_cost = data.get("package_cost")

        unit_cost_value = None
        package_cost_value = None

        # Prefer package_cost if provided
        if package_cost not in (None, "", "null"):
            try:
                package_cost_value = Decimal(str(package_cost))
            except (InvalidOperation, TypeError):
                package_cost_value = None
        # Fallback to unit_cost for backward compatibility
        elif unit_cost not in (None, "", "null"):
            try:
                unit_cost_value = Decimal(str(unit_cost))
            except (InvalidOperation, TypeError):
                unit_cost_value = None

        average_lead_time = data.get("average_lead_time")
        try:
            average_lead_time_value = (
                int(average_lead_time)
                if average_lead_time not in (None, "", "null")
                else ItemSupplier._meta.get_field("average_lead_time").default
            )
        except (ValueError, TypeError):
            average_lead_time_value = ItemSupplier._meta.get_field("average_lead_time").default

        quantity = data.get("quantity_per_package")
        try:
            quantity_value = (
                int(quantity)
                if quantity not in (None, "", "null")
                else ItemSupplier._meta.get_field("quantity_per_package").default
            )
        except (ValueError, TypeError):
            quantity_value = ItemSupplier._meta.get_field("quantity_per_package").default

        ItemSupplier.objects.update_or_create(
            item=item,
            supplier=supplier,
            defaults={
                "supplier_sku": data.get("supplier_sku") or item.sku or str(item.id),
                "supplier_url": data.get("supplier_url", ""),
                "unit_cost": unit_cost_value,
                "package_cost": package_cost_value,
                "average_lead_time": average_lead_time_value,
                "quantity_per_package": quantity_value,
                "package_upc": data.get("package_upc", ""),
                "unit_upc": data.get("unit_upc", ""),
                "is_primary": True,
            },
        )


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


class ItemSupplierViewSet(viewsets.ModelViewSet):
    """API endpoint for item-supplier relationships with pricing data."""
    
    queryset = ItemSupplier.objects.select_related("item", "supplier").prefetch_related("price_history").all()
    serializer_class = ItemSupplierSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by item if specified
        item_id = self.request.query_params.get("item_id")
        if item_id:
            queryset = queryset.filter(item_id=item_id)
            
        # Filter by supplier if specified
        supplier_id = self.request.query_params.get("supplier_id")
        if supplier_id:
            queryset = queryset.filter(supplier_id=supplier_id)
            
        # Filter to only active suppliers if requested
        active_only = self.request.query_params.get("active_only", "false").lower() == "true"
        if active_only:
            queryset = queryset.filter(is_active=True)
        
        return queryset.order_by("-is_primary", "unit_cost")
    
    @action(detail=True, methods=["get"])
    def price_history(self, request, pk=None):
        """Get full price history for this item-supplier relationship."""
        item_supplier = self.get_object()
        history = item_supplier.price_history.all()
        
        # Optional date filtering
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        
        if start_date:
            history = history.filter(recorded_at__gte=start_date)
        if end_date:
            history = history.filter(recorded_at__lte=end_date)
            
        serializer = PriceHistorySerializer(history, many=True)
        return Response(serializer.data)


class PriceHistoryViewSet(viewsets.ReadOnlyModelViewSet):
    """API endpoint for price history records (read-only)."""
    
    queryset = PriceHistory.objects.select_related("item_supplier__item", "item_supplier__supplier").all()
    serializer_class = PriceHistorySerializer
    permission_classes = [IsAuthenticatedOrReadOnly]
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by item if specified
        item_id = self.request.query_params.get("item_id")
        if item_id:
            queryset = queryset.filter(item_supplier__item_id=item_id)
            
        # Filter by supplier if specified
        supplier_id = self.request.query_params.get("supplier_id")
        if supplier_id:
            queryset = queryset.filter(item_supplier__supplier_id=supplier_id)
            
        # Filter by change type if specified
        change_type = self.request.query_params.get("change_type")
        if change_type:
            queryset = queryset.filter(change_type=change_type)
            
        # Date range filtering
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        
        if start_date:
            queryset = queryset.filter(recorded_at__gte=start_date)
        if end_date:
            queryset = queryset.filter(recorded_at__lte=end_date)
        
        return queryset.order_by("-recorded_at")
    
    @action(detail=False, methods=["get"])
    def recent_changes(self, request):
        """Get recent price changes across all items."""
        # Get price changes from the last 30 days by default
        try:
            days = int(request.query_params.get("days", 30))
        except (ValueError, TypeError):
            days = 30
        
        from django.utils import timezone
        from datetime import timedelta
        
        since_date = timezone.now() - timedelta(days=days)
        recent_changes = self.get_queryset().filter(
            recorded_at__gte=since_date,
            change_type="updated"  # Only actual price updates, not initial records
        )[:50]  # Limit to 50 most recent
        
        serializer = self.get_serializer(recent_changes, many=True)
        return Response(serializer.data)
