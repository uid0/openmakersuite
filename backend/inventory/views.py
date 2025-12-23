"""
Views for inventory API.
"""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.db import models, transaction
from django.db.models import F, Q
from django.http import HttpResponse
from django.utils import timezone

from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import (
    AllowAny,
    IsAdminUser,
    IsAuthenticated,
    IsAuthenticatedOrReadOnly,
)
from rest_framework.response import Response

from .models import (
    Asset,
    AssetPart,
    Category,
    Fixture,
    FixtureRefillRequest,
    InventoryItem,
    ItemSupplier,
    Location,
    PriceHistory,
    Supplier,
    UsageLog,
)
from .serializers import (
    AssetPartSerializer,
    AssetSerializer,
    CategorySerializer,
    FixtureDetailSerializer,
    FixtureRefillRequestSerializer,
    FixtureSerializer,
    InventoryItemDetailSerializer,
    InventoryItemSerializer,
    ItemSupplierSerializer,
    LocationSerializer,
    PriceHistorySerializer,
    SupplierDetailSerializer,
    SupplierSerializer,
    UsageLogSerializer,
)


class SupplierViewSet(viewsets.ModelViewSet):
    """API endpoint for suppliers."""

    queryset = Supplier.objects.prefetch_related("supplier_items").all()
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_serializer_class(self):
        """Use detail serializer for retrieve action."""
        if self.action == "retrieve":
            return SupplierDetailSerializer
        return SupplierSerializer

    def get_queryset(self):
        """Filter suppliers by type if specified."""
        queryset = super().get_queryset()

        # Filter by supplier_type if specified
        supplier_type = self.request.query_params.get("supplier_type")
        if supplier_type:
            queryset = queryset.filter(supplier_type=supplier_type)

        # Search functionality
        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(Q(name__icontains=search) | Q(notes__icontains=search))

        return queryset.order_by("name")

    @action(detail=True, methods=["get"])
    def analytics(self, request, pk=None):
        """Get detailed analytics for a supplier."""
        supplier = self.get_object()

        try:
            from datetime import timedelta
            from decimal import Decimal

            from django.db.models import Avg, Count, Max, Min, Sum
            from django.utils import timezone

            from reorder_queue.models import LeadTimeLog, PurchaseOrder

            # Lead time analytics
            lead_time_logs = LeadTimeLog.objects.filter(item_supplier__supplier=supplier)

            lead_time_stats = {}
            if lead_time_logs.exists():
                stats = lead_time_logs.aggregate(
                    avg_lead_time=Avg("actual_lead_time_days"),
                    min_lead_time=Min("actual_lead_time_days"),
                    max_lead_time=Max("actual_lead_time_days"),
                    avg_variance=Avg("variance_days"),
                    total_orders=Count("id"),
                )

                on_time_count = lead_time_logs.filter(variance_days__lte=0).count()
                on_time_percentage = (
                    (on_time_count / stats["total_orders"] * 100)
                    if stats["total_orders"] > 0
                    else None
                )

                lead_time_stats = {
                    "average_lead_time": (
                        float(stats["avg_lead_time"]) if stats["avg_lead_time"] else None
                    ),
                    "min_lead_time": stats["min_lead_time"],
                    "max_lead_time": stats["max_lead_time"],
                    "average_variance": (
                        float(stats["avg_variance"]) if stats["avg_variance"] else None
                    ),
                    "total_orders": stats["total_orders"],
                    "on_time_percentage": (
                        float(on_time_percentage) if on_time_percentage is not None else None
                    ),
                }

            # Price trends
            six_months_ago = timezone.now() - timedelta(days=180)
            price_history = PriceHistory.objects.filter(
                item_supplier__supplier=supplier, recorded_at__gte=six_months_ago
            ).order_by("-recorded_at")

            price_trends = {
                "recent_changes": [
                    {
                        "item_name": ph.item_supplier.item.name,
                        "recorded_at": ph.recorded_at.isoformat(),
                        "unit_cost": float(ph.unit_cost) if ph.unit_cost else None,
                        "package_cost": float(ph.package_cost) if ph.package_cost else None,
                        "change_type": ph.change_type,
                        "price_change_percentage": (
                            float(ph.price_change_percentage)
                            if ph.price_change_percentage
                            else None
                        ),
                    }
                    for ph in price_history[:20]
                ],
                "total_changes": price_history.count(),
            }

            # Order statistics
            purchase_orders = PurchaseOrder.objects.filter(supplier=supplier)
            order_stats = {
                "total_orders": purchase_orders.count(),
                "received_orders": purchase_orders.filter(status=PurchaseOrder.RECEIVED).count(),
                "total_spent": (
                    purchase_orders.filter(
                        status=PurchaseOrder.RECEIVED, actual_total__isnull=False
                    ).aggregate(total=Sum("actual_total"))["total"]
                    or Decimal("0.00")
                ),
            }

            return Response(
                {
                    "lead_time_analytics": lead_time_stats,
                    "price_trends": price_trends,
                    "order_statistics": order_stats,
                }
            )
        except ImportError:
            return Response(
                {
                    "lead_time_analytics": {},
                    "price_trends": {},
                    "order_statistics": {},
                }
            )


class CategoryViewSet(viewsets.ModelViewSet):
    """API endpoint for categories."""

    queryset = Category.objects.select_related("parent").prefetch_related("children").all()
    serializer_class = CategorySerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        """Return categories with optimized queries."""
        return Category.objects.select_related("parent").prefetch_related("children").all()


class LocationViewSet(viewsets.ModelViewSet):
    """API endpoint for locations."""

    queryset = Location.objects.all()
    serializer_class = LocationSerializer

    def get_queryset(self):
        """Filter queryset based on user permissions."""
        queryset = Location.objects.select_related("parent").prefetch_related("fixtures")
        # Public users only see active locations
        if not self.request.user.is_authenticated or not self.request.user.is_staff:
            queryset = queryset.filter(is_active=True)

        # Search functionality
        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(Q(name__icontains=search) | Q(description__icontains=search))

        return queryset

    def get_permissions(self):
        """Allow read for all, but require admin for write operations."""
        if self.action in ["create", "update", "partial_update", "destroy"]:
            return [IsAdminUser()]
        return [AllowAny()]

    def list(self, request):
        """List locations with hierarchy support."""
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], permission_classes=[AllowAny])
    def generate_qr(self, request, pk=None):
        """Generate or regenerate QR code for a location."""
        location = self.get_object()

        from .services.qr_code_service import QRCodeService
        from .utils.rate_limiting import QRCodeRateLimiter

        # Get user and IP for rate limiting
        user = request.user if request.user.is_authenticated else None
        ip_address = self._get_client_ip(request)

        # Check rate limit
        is_allowed, error_msg = QRCodeRateLimiter.check_rate_limit(
            user=user,
            item_id=str(location.id),
            item_type="location",
            ip_address=ip_address,
        )

        if not is_allowed:
            return Response(
                {"error": error_msg},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        try:
            include_logo = request.data.get("include_logo", True)
            service = QRCodeService(include_logo=include_logo)
            service.generate_for_location(location)
            return Response(
                {
                    "message": "QR code generated successfully",
                    "qr_code_url": location.qr_code.url if location.qr_code else None,
                }
            )
        except Exception as e:
            return Response(
                {"error": f"Failed to generate QR code: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _get_client_ip(self, request):
        """Get client IP address from request."""
        x_forwarded_for = request.headers.get("x-forwarded-for")
        if x_forwarded_for:
            ip = x_forwarded_for.split(",")[0]
        else:
            ip = request.META.get("REMOTE_ADDR")
        return ip

    @action(detail=True, methods=["get"], permission_classes=[AllowAny])
    def qr_code(self, request, pk=None):
        """Get QR code image for a location."""
        location = self.get_object()

        if not location.qr_code:
            return Response({"error": "QR code not generated yet"}, status=404)

        from django.http import HttpResponse

        response = HttpResponse(location.qr_code.read(), content_type="image/png")
        response["Content-Disposition"] = f'inline; filename="qr_{location.id}.png"'
        return response

    @action(detail=True, methods=["get"], permission_classes=[AllowAny])
    def fixtures(self, request, pk=None):
        """Get fixtures for a location."""
        location = self.get_object()
        fixtures = location.fixtures.filter(is_active=True)
        serializer = FixtureSerializer(fixtures, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get"], permission_classes=[AllowAny])
    def checklists(self, request, pk=None):
        """Get checklists associated with this location."""
        location = self.get_object()

        from checklists.models import Checklist
        from checklists.serializers import ChecklistListSerializer

        # Get checklists that have steps associated with this location
        checklists = Checklist.objects.filter(steps__location=location, is_active=True).distinct()

        # Filter by public access
        user = request.user
        if not user.is_authenticated:
            checklists = checklists.filter(is_public=True)
        else:
            from membership.utils import get_user_managed_sigs, is_logistics_member

            if not (user.is_superuser or user.is_staff or is_logistics_member(user)):
                user_sigs = get_user_managed_sigs(user)
                if user_sigs.exists():
                    from django.db import models

                    checklists = checklists.filter(
                        models.Q(is_public=True) | models.Q(sig__in=user_sigs)
                    )
                else:
                    checklists = checklists.filter(is_public=True)

        serializer = ChecklistListSerializer(checklists, many=True)
        return Response(serializer.data)


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
        if self.action in [
            "list",
            "retrieve",
            "low_stock",
            "reordered",
            "download_card",
            "log_usage",
            "generate_qr",
            "qr_code",
            "checklists",
            "scan",
        ]:
            return [AllowAny()]
        # Admin actions (create, update, delete)
        return [IsAuthenticated()]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return InventoryItemDetailSerializer
        return InventoryItemSerializer

    def get_queryset(self):
        queryset = (
            InventoryItem.objects.select_related("category", "location")
            .prefetch_related("item_suppliers__supplier")
            .all()
        )

        # Filter by SIG ownership for SIG admins
        user = self.request.user
        if user.is_authenticated and not (user.is_superuser or user.is_staff):
            from membership.utils import get_user_managed_sigs, is_logistics_member

            # Logistics can see everything
            if not is_logistics_member(user):
                # SIG admins can only see inventory items owned by their SIGs
                user_sigs = get_user_managed_sigs(user)
                if user_sigs.exists():
                    queryset = queryset.filter(owning_group__in=user_sigs)

        # Filter by category if specified
        category = self.request.query_params.get("category")
        if category:
            queryset = queryset.filter(category_id=category)

        # Filter by location if specified
        location = self.request.query_params.get("location")
        if location:
            queryset = queryset.filter(location_id=location)

        # Search functionality
        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(description__icontains=search)
                | Q(sku__icontains=search)
            )

        # Filter by low stock if specified
        low_stock = self.request.query_params.get("low_stock", "").lower()
        if low_stock == "true":
            queryset = queryset.filter(current_stock__lte=F("minimum_stock"))

        # Filter by active status if specified
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == "true")

        return queryset.order_by("name")

    def create(self, request, *args, **kwargs):
        # Check permissions
        user = request.user
        if not user.is_authenticated:
            return Response(
                {"detail": "Authentication required."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        from django.contrib.auth.models import Group

        from membership.utils import is_logistics_member, is_sig_admin

        # Check ownership_type if provided
        ownership_type = request.data.get("ownership_type")
        owning_group_id = request.data.get("owning_group")

        if ownership_type == "group" and owning_group_id:
            try:
                group = Group.objects.get(pk=owning_group_id)
                if not (
                    user.is_superuser
                    or user.is_staff
                    or is_logistics_member(user)
                    or is_sig_admin(user, group)
                ):
                    return Response(
                        {
                            "detail": "You do not have permission to create inventory items for this SIG."
                        },
                        status=status.HTTP_403_FORBIDDEN,
                    )
            except Group.DoesNotExist:
                return Response(
                    {"detail": "Group not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )

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

    def update(self, request, *args, **kwargs):
        """Update an inventory item with permission checks."""
        item = self.get_object()
        user = request.user

        if not user.is_authenticated:
            return Response(
                {"detail": "Authentication required."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        from django.contrib.auth.models import Group

        from membership.utils import can_manage_sig_inventory, is_logistics_member, is_sig_admin

        if not can_manage_sig_inventory(user, item):
            return Response(
                {"detail": "You do not have permission to modify this inventory item."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Check if trying to change ownership to a group user doesn't manage
        if "owning_group" in request.data:
            owning_group_id = request.data.get("owning_group")
            if owning_group_id:
                try:
                    group = Group.objects.get(pk=owning_group_id)
                    if not (
                        user.is_superuser
                        or user.is_staff
                        or is_logistics_member(user)
                        or is_sig_admin(user, group)
                    ):
                        return Response(
                            {
                                "detail": "You do not have permission to assign inventory items to this SIG."
                            },
                            status=status.HTTP_403_FORBIDDEN,
                        )
                except Group.DoesNotExist:
                    return Response(
                        {"detail": "Group not found."},
                        status=status.HTTP_404_NOT_FOUND,
                    )

        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        """Delete an inventory item with permission checks."""
        item = self.get_object()
        user = request.user

        if not user.is_authenticated:
            return Response(
                {"detail": "Authentication required."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        from membership.utils import can_manage_sig_inventory

        if not can_manage_sig_inventory(user, item):
            return Response(
                {"detail": "You do not have permission to delete this inventory item."},
                status=status.HTTP_403_FORBIDDEN,
            )

        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated])
    def my_sig_inventory(self, request):
        """List inventory items for SIGs the user administers."""
        from membership.utils import get_user_managed_sigs

        user = request.user
        user_sigs = get_user_managed_sigs(user)

        if not user_sigs.exists():
            return Response(
                {"detail": "You are not an admin of any SIGs."},
                status=status.HTTP_403_FORBIDDEN,
            )

        items = self.get_queryset().filter(owning_group__in=user_sigs)
        serializer = self.get_serializer(items, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def generate_qr(self, request, pk=None):
        """Generate or regenerate QR code for an item."""
        item = self.get_object()

        # Generate QR code synchronously for immediate response
        from .services.qr_code_service import QRCodeService
        from .utils.rate_limiting import QRCodeRateLimiter

        # Get user and IP for rate limiting
        user = request.user if request.user.is_authenticated else None
        ip_address = self._get_client_ip(request)

        # Check rate limit
        is_allowed, error_msg = QRCodeRateLimiter.check_rate_limit(
            user=user,
            item_id=str(item.id),
            item_type="item",
            ip_address=ip_address,
        )

        if not is_allowed:
            return Response(
                {"error": error_msg},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        try:
            include_logo = request.data.get("include_logo", True)
            service = QRCodeService(include_logo=include_logo)
            service.generate_for_item(item)
            return Response(
                {
                    "status": "QR code generated successfully",
                    "qr_code_url": (
                        request.build_absolute_uri(item.qr_code.url) if item.qr_code else None
                    ),
                }
            )
        except Exception as e:
            return Response({"error": str(e)}, status=500)

    def _get_client_ip(self, request):
        """Get client IP address from request."""
        x_forwarded_for = request.headers.get("x-forwarded-for")
        if x_forwarded_for:
            ip = x_forwarded_for.split(",")[0]
        else:
            ip = request.META.get("REMOTE_ADDR")
        return ip

    @action(
        detail=True,
        methods=["get"],
        url_path="qr_code",
        url_name="qr_code",
        name="QR Code",
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

    @action(detail=True, methods=["get"], permission_classes=[AllowAny])
    def checklists(self, request, pk=None):
        """Get checklists associated with this inventory item."""
        item = self.get_object()

        from checklists.models import Checklist
        from checklists.serializers import ChecklistListSerializer

        # Get checklists that have steps associated with this item
        checklists = Checklist.objects.filter(steps__inventory_item=item, is_active=True).distinct()

        # Filter by public access
        user = request.user
        if not user.is_authenticated:
            checklists = checklists.filter(is_public=True)
        else:
            from membership.utils import get_user_managed_sigs, is_logistics_member

            if not (user.is_superuser or user.is_staff or is_logistics_member(user)):
                user_sigs = get_user_managed_sigs(user)
                if user_sigs.exists():
                    from django.db import models

                    checklists = checklists.filter(
                        models.Q(is_public=True) | models.Q(sig__in=user_sigs)
                    )
                else:
                    checklists = checklists.filter(is_public=True)

        serializer = ChecklistListSerializer(checklists, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], permission_classes=[AllowAny])
    def scan(self, request, pk=None):
        """
        Handle inventory item QR code scan.
        Updates last_scanned_at timestamp and returns item information.
        Anyone can scan (AllowAny).
        """
        from django.utils import timezone

        item = self.get_object()

        # Update last scanned timestamp
        item.last_scanned_at = timezone.now()
        item.save(update_fields=["last_scanned_at"])

        # Return item data
        serializer = self.get_serializer(item)
        return Response(serializer.data)

    @action(
        detail=True,
        methods=["get"],
        url_path="download_card",
        url_name="download_card",
        name="Download Card",
    )
    def download_card(self, request, pk=None):
        """Generate and download Avery 5388 compatible index card PDF."""
        item = self.get_object()

        # Check if blank card is requested
        blank_card = request.GET.get("blank", "false").lower() == "true"

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
        name="Download Blank Card",
    )
    def download_blank_card(self, request, pk=None):
        """Generate and download blank card with only QR code for creative customization."""
        item = self.get_object()

        # Generate blank card PDF using the index cards system
        from index_cards.services import IndexCardRenderer

        renderer = IndexCardRenderer(blank_cards=True)
        pdf_bytes = renderer.render_preview(item, blank_card=True)

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="blank_card_{item.sku or item.id}.pdf"'
        )
        return response

    @action(detail=False, methods=["get"])
    def low_stock(self, request):
        """Get items that need reordering."""
        low_stock_items = [
            item for item in self.filter_queryset(self.get_queryset()) if item.needs_reorder
        ]
        serializer = self.get_serializer(low_stock_items, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"])
    def reordered(self, request):
        """Get items that have active reorder requests (pending, approved, or ordered)."""
        # Get items that have active reorder requests
        reordered_items = []
        for item in self.filter_queryset(self.get_queryset()):
            if item.has_pending_reorder():
                reordered_items.append(item)

        serializer = self.get_serializer(reordered_items, many=True)
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
        """Sync primary supplier data with reduced complexity."""
        supplier = self._validate_supplier(data.get("supplier"))
        if not supplier:
            return

        cost_data = self._process_cost_data(data)
        lead_time = self._process_lead_time_value(data.get("average_lead_time"))
        quantity = self._process_quantity_value(data.get("quantity_per_package"))

        self._create_supplier_relationship(item, supplier, data, cost_data, lead_time, quantity)

    def _validate_supplier(self, supplier_id):
        """Validate and return supplier or None if invalid."""
        if not supplier_id:
            return None
        try:
            return Supplier.objects.get(pk=supplier_id)
        except Supplier.DoesNotExist:
            return None

    def _process_cost_data(self, data):
        """Process unit and package cost data, preferring package_cost."""
        unit_cost = data.get("unit_cost")
        package_cost = data.get("package_cost")

        # Prefer package_cost if provided
        if package_cost not in (None, "", "null"):
            return self._safe_decimal_conversion(package_cost), None
        # Fallback to unit_cost for backward compatibility
        elif unit_cost not in (None, "", "null"):
            return None, self._safe_decimal_conversion(unit_cost)

        return None, None

    def _safe_decimal_conversion(self, value):
        """Safely convert value to Decimal or return None."""
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError):
            return None

    def _process_lead_time_value(self, lead_time):
        """Process and validate lead time value."""
        try:
            return (
                int(lead_time)
                if lead_time not in (None, "", "null")
                else ItemSupplier._meta.get_field("average_lead_time").default
            )
        except (ValueError, TypeError):
            return ItemSupplier._meta.get_field("average_lead_time").default

    def _process_quantity_value(self, quantity):
        """Process and validate quantity per package value."""
        try:
            return (
                int(quantity)
                if quantity not in (None, "", "null")
                else ItemSupplier._meta.get_field("quantity_per_package").default
            )
        except (ValueError, TypeError):
            return ItemSupplier._meta.get_field("quantity_per_package").default

    def _create_supplier_relationship(self, item, supplier, data, cost_data, lead_time, quantity):
        """Create or update the ItemSupplier relationship."""
        package_cost_value, unit_cost_value = cost_data

        ItemSupplier.objects.update_or_create(
            item=item,
            supplier=supplier,
            defaults={
                "supplier_sku": data.get("supplier_sku") or item.sku or str(item.id),
                "supplier_url": data.get("supplier_url", ""),
                "unit_cost": unit_cost_value,
                "package_cost": package_cost_value,
                "average_lead_time": lead_time,
                "quantity_per_package": quantity,
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

    queryset = (
        ItemSupplier.objects.select_related("item", "supplier")
        .prefetch_related("price_history")
        .all()
    )
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
            queryset = queryset.filter(is_active=True, is_discontinued=False)

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

    @action(detail=True, methods=["post"])
    def mark_discontinued(self, request, pk=None):
        """Mark this item as discontinued from this supplier."""
        item_supplier = self.get_object()
        item_supplier.is_discontinued = True
        item_supplier.is_active = False  # Also mark as inactive
        item_supplier.save()

        serializer = self.get_serializer(item_supplier)
        return Response(serializer.data)


class PriceHistoryViewSet(viewsets.ReadOnlyModelViewSet):
    """API endpoint for price history records (read-only)."""

    queryset = PriceHistory.objects.select_related(
        "item_supplier__item", "item_supplier__supplier"
    ).all()
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
        start_date = self.request.query_params.get("start_date")
        end_date = self.request.query_params.get("end_date")

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

        from datetime import timedelta

        from django.utils import timezone

        since_date = timezone.now() - timedelta(days=days)
        recent_changes = self.get_queryset().filter(
            recorded_at__gte=since_date,
            change_type="updated",  # Only actual price updates, not initial records
        )[
            :50
        ]  # Limit to 50 most recent

        serializer = self.get_serializer(recent_changes, many=True)
        return Response(serializer.data)


class AssetViewSet(viewsets.ModelViewSet):
    """API endpoint for hard assets."""

    queryset = (
        Asset.objects.select_related("inventory_item", "category", "location", "manufacturer")
        .prefetch_related("asset_parts__part", "asset_parts__part__category")
        .all()
    )
    serializer_class = AssetSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        queryset = super().get_queryset()

        # Filter by SIG ownership for SIG admins
        user = self.request.user

        # Superusers and staff can see all assets (no filtering applied)
        if user.is_authenticated and not (user.is_superuser or user.is_staff):
            from membership.utils import get_user_managed_sigs, is_logistics_member

            # Logistics can see everything
            if not is_logistics_member(user):
                # SIG admins can only see assets owned by their SIGs
                # Regular users (non-SIG admins) can see all assets including space-owned
                user_sigs = get_user_managed_sigs(user)
                if user_sigs.exists():
                    # SIG admin: only show assets owned by their SIGs
                    queryset = queryset.filter(owning_group__in=user_sigs)
                # If user_sigs doesn't exist, user is a regular authenticated user
                # and should see all assets (no filtering needed)

        # Filter by category if specified
        category = self.request.query_params.get("category")
        if category:
            queryset = queryset.filter(category_id=category)

        # Filter by location if specified
        location = self.request.query_params.get("location")
        if location:
            queryset = queryset.filter(location_id=location)

        # Filter by status if specified
        asset_status = self.request.query_params.get("status")
        if asset_status:
            queryset = queryset.filter(status=asset_status)

        # Filter by inventory_item if specified
        inventory_item = self.request.query_params.get("inventory_item")
        if inventory_item:
            queryset = queryset.filter(inventory_item_id=inventory_item)

        # Filter by manufacturer if specified
        manufacturer = self.request.query_params.get("manufacturer")
        if manufacturer:
            queryset = queryset.filter(manufacturer_id=manufacturer)

        # Filter by owning_group if specified
        owning_group = self.request.query_params.get("owning_group")
        if owning_group:
            queryset = queryset.filter(owning_group_id=owning_group)

        # Filter by date_received range
        date_received_after = self.request.query_params.get("date_received_after")
        if date_received_after:
            try:
                from datetime import datetime

                date_obj = datetime.fromisoformat(date_received_after.replace("Z", "+00:00")).date()
                queryset = queryset.filter(date_received__gte=date_obj)
            except (ValueError, AttributeError):
                pass  # Invalid date format, ignore filter

        date_received_before = self.request.query_params.get("date_received_before")
        if date_received_before:
            try:
                from datetime import datetime

                date_obj = datetime.fromisoformat(
                    date_received_before.replace("Z", "+00:00")
                ).date()
                queryset = queryset.filter(date_received__lte=date_obj)
            except (ValueError, AttributeError):
                pass  # Invalid date format, ignore filter

        # Filter by age (days since date_received)
        age_min_days = self.request.query_params.get("age_min_days")
        if age_min_days:
            try:
                min_days = int(age_min_days)
                cutoff_date = date.today() - timedelta(days=min_days)
                queryset = queryset.filter(date_received__lte=cutoff_date)
            except (ValueError, TypeError):
                pass  # Invalid value, ignore filter

        age_max_days = self.request.query_params.get("age_max_days")
        if age_max_days:
            try:
                max_days = int(age_max_days)
                cutoff_date = date.today() - timedelta(days=max_days)
                queryset = queryset.filter(date_received__gte=cutoff_date)
            except (ValueError, TypeError):
                pass  # Invalid value, ignore filter

        # Search functionality
        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(description__icontains=search)
                | Q(serial_number__icontains=search)
                | Q(asset_tag__icontains=search)
                | Q(manufacturer_name__icontains=search)
            )

        # Filter by active status if specified
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == "true")

        # Ordering support
        ordering = self.request.query_params.get("ordering", "name")
        # Validate ordering field to prevent SQL injection
        # Only allow ordering by direct fields or related fields that are select_related
        valid_ordering_fields = {
            "name",
            "-name",
            "asset_tag",
            "-asset_tag",
            "serial_number",
            "-serial_number",
            "status",
            "-status",
            "date_received",
            "-date_received",
            "created_at",
            "-created_at",
            "is_active",
            "-is_active",
            "location__name",
            "-location__name",
            "category__name",
            "-category__name",
            "manufacturer__name",
            "-manufacturer__name",
            "manufacturer_name",
            "-manufacturer_name",
            "inventory_item__name",
            "-inventory_item__name",
            "owning_group__name",
            "-owning_group__name",
        }
        if ordering in valid_ordering_fields:
            queryset = queryset.order_by(ordering)
        else:
            # Default ordering
            queryset = queryset.order_by("name")

        return queryset

    def create(self, request, *args, **kwargs):
        """Create a new asset with permission checks."""
        # Check if user can create assets (must be admin, logistics, or SIG admin)
        user = request.user
        if not user.is_authenticated:
            return Response(
                {"detail": "Authentication required."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        from membership.utils import is_logistics_member

        # Check ownership_type if provided
        ownership_type = request.data.get("ownership_type")
        owning_group_id = request.data.get("owning_group")

        if ownership_type == "group" and owning_group_id:
            # Check if user is admin of the specified group
            from django.contrib.auth.models import Group

            from membership.utils import is_sig_admin

            try:
                group = Group.objects.get(pk=owning_group_id)
                if not (
                    user.is_superuser
                    or user.is_staff
                    or is_logistics_member(user)
                    or is_sig_admin(user, group)
                ):
                    return Response(
                        {"detail": "You do not have permission to create assets for this SIG."},
                        status=status.HTTP_403_FORBIDDEN,
                    )
            except Group.DoesNotExist:
                return Response(
                    {"detail": "Group not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )

        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        """Update an asset with permission checks."""
        asset = self.get_object()
        user = request.user

        if not user.is_authenticated:
            return Response(
                {"detail": "Authentication required."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        from membership.utils import can_manage_sig_asset

        if not can_manage_sig_asset(user, asset):
            return Response(
                {"detail": "You do not have permission to modify this asset."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Check if trying to change ownership to a group user doesn't manage
        if "owning_group" in request.data:
            owning_group_id = request.data.get("owning_group")
            if owning_group_id:
                from django.contrib.auth.models import Group

                from membership.utils import is_logistics_member, is_sig_admin

                try:
                    group = Group.objects.get(pk=owning_group_id)
                    if not (
                        user.is_superuser
                        or user.is_staff
                        or is_logistics_member(user)
                        or is_sig_admin(user, group)
                    ):
                        return Response(
                            {"detail": "You do not have permission to assign assets to this SIG."},
                            status=status.HTTP_403_FORBIDDEN,
                        )
                except Group.DoesNotExist:
                    return Response(
                        {"detail": "Group not found."},
                        status=status.HTTP_404_NOT_FOUND,
                    )

        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        """Delete an asset with permission checks."""
        asset = self.get_object()
        user = request.user

        if not user.is_authenticated:
            return Response(
                {"detail": "Authentication required."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        from membership.utils import can_manage_sig_asset

        if not can_manage_sig_asset(user, asset):
            return Response(
                {"detail": "You do not have permission to delete this asset."},
                status=status.HTTP_403_FORBIDDEN,
            )

        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated])
    def my_sig_assets(self, request):
        """List assets for SIGs the user administers."""
        from membership.utils import get_user_managed_sigs

        user = request.user
        user_sigs = get_user_managed_sigs(user)

        if not user_sigs.exists():
            return Response(
                {"detail": "You are not an admin of any SIGs."},
                status=status.HTTP_403_FORBIDDEN,
            )

        assets = self.get_queryset().filter(owning_group__in=user_sigs)
        serializer = self.get_serializer(assets, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], permission_classes=[AllowAny])
    def generate_qr(self, request, pk=None):
        """Generate or regenerate QR code for an asset."""
        asset = self.get_object()

        # Generate QR code synchronously for immediate response
        from .services.qr_code_service import QRCodeService
        from .utils.rate_limiting import QRCodeRateLimiter

        # Get user and IP for rate limiting
        user = request.user if request.user.is_authenticated else None
        ip_address = self._get_client_ip(request)

        # Check rate limit
        is_allowed, error_msg = QRCodeRateLimiter.check_rate_limit(
            user=user,
            item_id=str(asset.id),
            item_type="asset",
            ip_address=ip_address,
        )

        if not is_allowed:
            return Response(
                {"error": error_msg},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        try:
            include_logo = request.data.get("include_logo", True)
            service = QRCodeService(include_logo=include_logo)
            service.generate_for_asset(asset)
            serializer = self.get_serializer(asset)
            return Response(serializer.data)
        except Exception as e:
            return Response(
                {"error": f"Failed to generate QR code: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _get_client_ip(self, request):
        """Get client IP address from request."""
        x_forwarded_for = request.headers.get("x-forwarded-for")
        if x_forwarded_for:
            ip = x_forwarded_for.split(",")[0]
        else:
            ip = request.META.get("REMOTE_ADDR")
        return ip

    @action(detail=True, methods=["get"])
    def qr_code(self, request, pk=None):
        """Get the QR code image for an asset."""
        asset = self.get_object()

        if not asset.qr_code:
            return Response(
                {"error": "QR code not generated yet"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Return the QR code image
        response = HttpResponse(asset.qr_code.read(), content_type="image/png")
        response["Content-Disposition"] = f'attachment; filename="asset-{asset.asset_tag}-qr.png"'
        return response

    @action(detail=True, methods=["get"])
    def download_label(self, request, pk=None):
        """Generate and download a Brother QL-820nwb label for an asset."""
        asset = self.get_object()

        from .utils.label_generator import BrotherLabelRenderer

        try:
            renderer = BrotherLabelRenderer()
            pdf_bytes = renderer.render_label(asset)
            identifier = asset.asset_tag or str(asset.id)[:8]
            filename = f"asset_label_{identifier}.pdf"

            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            return response
        except Exception as e:
            return Response(
                {"error": f"Failed to generate label: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=False, methods=["get"])
    def not_checked_in(self, request):
        """Get assets that haven't been checked in for 3+ months."""
        from datetime import timedelta

        from django.utils import timezone

        three_months_ago = timezone.now() - timedelta(days=90)

        # Assets that have never been scanned or were scanned more than 3 months ago
        assets = (
            Asset.objects.filter(
                Q(last_scanned_at__lt=three_months_ago) | Q(last_scanned_at__isnull=True)
            )
            .filter(is_active=True)
            .select_related("category", "location", "manufacturer", "inventory_item")
        )

        # Filter by status if specified
        status = request.query_params.get("status")
        if status:
            assets = assets.filter(status=status)

        # Filter by inventory_item if specified
        inventory_item = request.query_params.get("inventory_item")
        if inventory_item:
            assets = assets.filter(inventory_item_id=inventory_item)

        serializer = self.get_serializer(assets, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def download_label_escp(self, request, pk=None):
        """Generate and download ESC/P commands for direct printing to Brother QL-820NWB."""
        asset = self.get_object()

        from .utils.brother_esc_p import BrotherESCPGenerator

        try:
            generator = BrotherESCPGenerator()
            escp_bytes = generator.generate_label_commands(asset)
            identifier = asset.asset_tag or str(asset.id)[:8]
            filename = f"asset_label_{identifier}.escp"

            response = HttpResponse(escp_bytes, content_type="application/octet-stream")
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            return response
        except Exception as e:
            return Response(
                {"error": f"Failed to generate ESC/P commands: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["get"])
    def download_label_preview(self, request, pk=None):
        """Generate and download PNG preview of label for Brother QL-820NWB."""
        asset = self.get_object()

        from .utils.brother_esc_p import BrotherESCPGenerator

        try:
            generator = BrotherESCPGenerator()
            png_bytes = generator.generate_label_file(asset, output_format="png")
            identifier = asset.asset_tag or str(asset.id)[:8]
            filename = f"asset_label_{identifier}_preview.png"

            response = HttpResponse(png_bytes, content_type="image/png")
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            return response
        except Exception as e:
            return Response(
                {"error": f"Failed to generate label preview: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=False, methods=["post"])
    def download_labels_batch(self, request):
        """Generate and download labels for multiple assets."""
        asset_ids = request.data.get("asset_ids", [])
        if not asset_ids:
            return Response(
                {"error": "asset_ids list is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from .utils.label_generator import BrotherLabelRenderer

        try:
            assets = Asset.objects.filter(id__in=asset_ids)
            if not assets.exists():
                return Response({"error": "No assets found"}, status=status.HTTP_404_NOT_FOUND)

            renderer = BrotherLabelRenderer()
            pdf_bytes = renderer.render_batch(list(assets))
            filename = "asset_labels_batch.pdf"

            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            return response
        except Exception as e:
            return Response(
                {"error": f"Failed to generate labels: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["post"], permission_classes=[AllowAny])
    def scan(self, request, pk=None):
        """
        Handle asset QR code scan.
        Updates last_scanned_at timestamp and returns asset information.
        Anyone can scan (AllowAny).
        """
        from django.utils import timezone

        asset = self.get_object()

        # Update last scanned timestamp
        asset.last_scanned_at = timezone.now()
        asset.save(update_fields=["last_scanned_at"])

        # Return asset data
        serializer = self.get_serializer(asset)
        return Response(serializer.data)

    @action(detail=True, methods=["get"], permission_classes=[AllowAny])
    def checklists(self, request, pk=None):
        """Get checklists associated with this asset."""
        asset = self.get_object()

        from checklists.models import Checklist
        from checklists.serializers import ChecklistListSerializer

        # Get checklists that have steps associated with this asset
        checklists = Checklist.objects.filter(steps__asset=asset, is_active=True).distinct()

        # Filter by public access
        user = request.user
        if not user.is_authenticated:
            checklists = checklists.filter(is_public=True)
        else:
            from membership.utils import get_user_managed_sigs, is_logistics_member

            if not (user.is_superuser or user.is_staff or is_logistics_member(user)):
                user_sigs = get_user_managed_sigs(user)
                if user_sigs.exists():
                    from django.db import models

                    checklists = checklists.filter(
                        models.Q(is_public=True) | models.Q(sig__in=user_sigs)
                    )
                else:
                    checklists = checklists.filter(is_public=True)

        serializer = ChecklistListSerializer(checklists, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def enable(self, request, pk=None):
        """Enable an asset (set is_active=True)."""
        asset = self.get_object()
        user = request.user

        if not user.is_authenticated:
            return Response(
                {"error": "Authentication required"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Check if user can operate assets in Implementing/Testing status
        if asset.status in [asset.IMPLEMENTING, asset.TESTING]:
            if not asset.can_user_operate(user):
                return Response(
                    {
                        "error": "Only Maintainers, Group Admins, Logistics, or COO can operate assets in Implementing or Testing status"
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

        # Check if user can enable this asset
        # Admins can always enable
        if not asset.is_user_admin(user):
            # Check if user's groups are in groups_can_enable
            user_groups = user.groups.all()
            if asset.groups_can_enable.exists():
                # If groups are specified, user must be in one of them
                if not any(group in asset.groups_can_enable.all() for group in user_groups):
                    return Response(
                        {"error": "You do not have permission to enable this asset"},
                        status=status.HTTP_403_FORBIDDEN,
                    )

        asset.is_active = True
        asset.save(update_fields=["is_active"])
        serializer = self.get_serializer(asset)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def disable(self, request, pk=None):
        """Disable an asset (set is_active=False)."""
        asset = self.get_object()
        user = request.user

        if not user.is_authenticated:
            return Response(
                {"error": "Authentication required"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Check if user can operate assets in Implementing/Testing status
        if asset.status in [asset.IMPLEMENTING, asset.TESTING]:
            if not asset.can_user_operate(user):
                return Response(
                    {
                        "error": "Only Maintainers, Group Admins, Logistics, or COO can operate assets in Implementing or Testing status"
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

        asset.is_active = False
        asset.save(update_fields=["is_active"])
        serializer = self.get_serializer(asset)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def lock(self, request, pk=None):
        """Lock an asset using ForgeKey lockout system."""
        from forgekey.models import DeviceLockout, OperationalMode

        asset = self.get_object()
        user = request.user
        reason = request.data.get("reason", "")

        if not user.is_authenticated:
            return Response(
                {"error": "Authentication required"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not reason:
            return Response({"error": "reason is required"}, status=status.HTTP_400_BAD_REQUEST)

        # Determine lockout level based on user's role
        lockout_level = self._get_user_lockout_level(user, asset)

        # Create lockout
        DeviceLockout.objects.create(
            asset=asset,
            locked_by=user,
            lockout_level=lockout_level,
            reason=reason,
        )

        # Update operational mode
        mode, _ = OperationalMode.objects.get_or_create(asset=asset)
        mode.mode = OperationalMode.MODE_LOCKED_OUT
        mode.save()

        serializer = self.get_serializer(asset)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def unlock(self, request, pk=None):
        """Unlock an asset using ForgeKey lockout system."""
        from forgekey.models import DeviceLockout, OperationalMode

        asset = self.get_object()
        user = request.user

        if not user.is_authenticated:
            return Response(
                {"error": "Authentication required"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Get active lockouts
        active_lockouts = DeviceLockout.objects.filter(asset=asset, is_active=True)

        if not active_lockouts.exists():
            return Response({"error": "Asset is not locked"}, status=status.HTTP_400_BAD_REQUEST)

        # Check if user can unlock any of the lockouts
        unlockable_lockout = None
        for lockout in active_lockouts:
            if lockout.can_be_unlocked_by(user):
                unlockable_lockout = lockout
                break

        if not unlockable_lockout:
            return Response(
                {"error": "You do not have permission to unlock this asset"},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Unlock the lockout
        unlockable_lockout.is_active = False
        unlockable_lockout.unlocked_at = timezone.now()
        unlockable_lockout.unlocked_by = user
        unlockable_lockout.save()

        # Check if there are other active lockouts
        remaining_lockouts = DeviceLockout.objects.filter(asset=asset, is_active=True)

        # If no more active lockouts, update operational mode
        if not remaining_lockouts.exists():
            try:
                mode = OperationalMode.objects.get(asset=asset)
                if mode.mode == OperationalMode.MODE_LOCKED_OUT:
                    mode.mode = OperationalMode.MODE_AVAILABLE
                    mode.save()
            except OperationalMode.DoesNotExist:
                pass

        serializer = self.get_serializer(asset)
        return Response(serializer.data)

    def _get_user_lockout_level(self, user, asset):
        """Determine the lockout level for a user."""
        from django.contrib.auth.models import Group

        from forgekey.models import LockoutLevel

        # Check for COO
        try:
            coo_group = Group.objects.get(name="COO")
            if coo_group in user.groups.all() or user.is_superuser:
                return LockoutLevel.COO
        except Group.DoesNotExist:
            if user.is_superuser:
                return LockoutLevel.COO

        # Check for Logistics Lead
        try:
            logistics_lead_group = Group.objects.get(name="Logistics Lead")
            if logistics_lead_group in user.groups.all():
                return LockoutLevel.LOGISTICS_LEAD
        except Group.DoesNotExist:
            pass

        # Check for Logistics Team
        try:
            logistics_group = Group.objects.get(name="Logistics")
            if logistics_group in user.groups.all():
                return LockoutLevel.LOGISTICS_TEAM
        except Group.DoesNotExist:
            pass

        # Check for Group Admin
        if asset.owning_group and asset.owning_group in user.groups.all():
            if (
                user.has_perm("inventory.group_admin")
                or user.groups.filter(name__endswith="_admin").exists()
            ):
                return LockoutLevel.GROUP_ADMIN

        # Check for Maintainer
        try:
            maintainer_group = Group.objects.get(name="Maintainer")
            if maintainer_group in user.groups.all():
                return LockoutLevel.MAINTAINER
        except Group.DoesNotExist:
            pass

        # Default to user level
        return LockoutLevel.USER

    @action(detail=True, methods=["post"])
    def report_problem(self, request, pk=None):
        """Report a problem with an asset."""
        asset = self.get_object()
        description = request.data.get("description", "")
        if not description:
            return Response(
                {"error": "description is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from .models import AssetProblem

        reported_by = ""
        if request.user and request.user.is_authenticated:
            reported_by = request.user.username

        problem = AssetProblem.objects.create(
            asset=asset,
            reported_by=reported_by,
            description=description,
        )

        # Send webhook notification if configured
        try:
            from reorder_queue.tasks import send_asset_problem_webhook

            send_asset_problem_webhook.delay(str(problem.id))
        except Exception as e:
            # Log but don't fail the request if webhook fails
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to send asset problem webhook: {e}", exc_info=True)

        from .serializers import AssetProblemSerializer

        serializer = AssetProblemSerializer(problem)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], permission_classes=[IsAuthenticatedOrReadOnly])
    def get_problems(self, request, pk=None):
        """Get list of problems for an asset."""
        asset = self.get_object()
        from .models import AssetProblem
        from .serializers import AssetProblemSerializer

        problems = AssetProblem.objects.filter(asset=asset).order_by("-created_at")
        serializer = AssetProblemSerializer(problems, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def resolve_problem(self, request, pk=None):
        """Resolve a problem for an asset."""
        asset = self.get_object()
        from .models import AssetProblem
        from .serializers import AssetProblemSerializer

        problem_id = request.data.get("problem_id")
        if not problem_id:
            return Response(
                {"error": "problem_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            problem = AssetProblem.objects.get(id=problem_id, asset=asset)
        except AssetProblem.DoesNotExist:
            return Response(
                {"error": "Problem not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Update problem status and resolution details
        new_status = request.data.get("status", AssetProblem.RESOLVED)
        if new_status not in [AssetProblem.RESOLVED, AssetProblem.CLOSED]:
            return Response(
                {
                    "error": f"Invalid status. Must be '{AssetProblem.RESOLVED}' or '{AssetProblem.CLOSED}'"
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        problem.status = new_status
        problem.resolution_notes = request.data.get(
            "resolution_notes", problem.resolution_notes or ""
        )

        # Set resolved_at and resolved_by if resolving for the first time
        if new_status in [AssetProblem.RESOLVED, AssetProblem.CLOSED] and not problem.resolved_at:
            problem.resolved_at = timezone.now()
            # Set resolved_by using handle or username
            if request.user.is_authenticated:
                problem.resolved_by = request.user.handle or request.user.username

        problem.save()

        serializer = AssetProblemSerializer(problem)
        return Response(serializer.data, status=status.HTTP_200_OK)


class AssetPartViewSet(viewsets.ModelViewSet):
    """API endpoint for asset parts/consumables."""

    queryset = AssetPart.objects.select_related("asset", "part", "part__category").all()
    serializer_class = AssetPartSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        queryset = super().get_queryset()

        # Filter by asset if specified
        asset = self.request.query_params.get("asset")
        if asset:
            queryset = queryset.filter(asset_id=asset)

        # Filter by part if specified
        part = self.request.query_params.get("part")
        if part:
            queryset = queryset.filter(part_id=part)

        # Filter by required status if specified
        is_required = self.request.query_params.get("is_required")
        if is_required is not None:
            queryset = queryset.filter(is_required=is_required.lower() == "true")

        # Note: needs_replacement filter is a calculated property, so we can't filter
        # efficiently in the database. If needed, this would require evaluating the queryset.
        # For now, we'll skip this filter to maintain queryset laziness.

        # Search functionality
        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(asset__name__icontains=search)
                | Q(asset__asset_tag__icontains=search)
                | Q(part__name__icontains=search)
                | Q(part__sku__icontains=search)
                | Q(notes__icontains=search)
            )

        return queryset.order_by("asset__name", "part__name")

    @action(detail=True, methods=["post"])
    def mark_replaced(self, request, pk=None):
        """Mark a part as replaced (updates last_replaced_at to now)."""
        from django.utils import timezone

        asset_part = self.get_object()
        asset_part.last_replaced_at = timezone.now()
        asset_part.save(update_fields=["last_replaced_at"])

        serializer = self.get_serializer(asset_part)
        return Response(serializer.data)


class FixtureViewSet(viewsets.ModelViewSet):
    """API endpoint for fixtures (refillable assets)."""

    queryset = Fixture.objects.select_related("location", "refill_item").all()
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return FixtureDetailSerializer
        return FixtureSerializer

    def get_queryset(self):
        queryset = super().get_queryset()

        # Filter by location if specified
        location = self.request.query_params.get("location")
        if location:
            queryset = queryset.filter(location_id=location)

        # Filter by active status if specified
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == "true")

        # Search functionality
        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(description__icontains=search)
                | Q(asset_tag__icontains=search)
                | Q(location__name__icontains=search)
            )

        return queryset.order_by("location__name", "name")

    @action(
        detail=True,
        methods=["get"],
        url_path="download_card",
        url_name="download_card",
        name="Download Fixture Card",
    )
    def download_card(self, request, pk=None):
        """Generate and download a fixture refill request card."""
        fixture = self.get_object()

        from index_cards.services import FixtureCardRenderer

        renderer = FixtureCardRenderer()
        pdf_bytes = renderer.render_preview(fixture)
        identifier = fixture.asset_tag or fixture.id
        filename = f"fixture_card_{identifier}.pdf"

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    @action(detail=True, methods=["post"], permission_classes=[AllowAny])
    def scan(self, request, pk=None):
        """
        Create a refill request when a fixture QR code is scanned.
        Anyone can scan (AllowAny).
        """
        fixture = self.get_object()

        if not fixture.is_active:
            return Response(
                {"error": "This fixture is inactive"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get username from request if authenticated
        requested_by = ""
        if request.user and request.user.is_authenticated:
            requested_by = request.user.username

        # Create the refill request
        notes = request.data.get("notes", "")
        refill_request = FixtureRefillRequest.objects.create(
            fixture=fixture, requested_by=requested_by, notes=notes
        )

        # Send webhook notification
        from reorder_queue.tasks import send_fixture_refill_webhook

        try:
            send_fixture_refill_webhook.delay(str(refill_request.id))
        except Exception as e:  # nosec B110
            # Log but don't fail the request if webhook fails
            # This is intentional - webhook failures should not block refill requests
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to send fixture refill webhook: {e}", exc_info=True)

        serializer = FixtureRefillRequestSerializer(refill_request)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def resolve_all(self, request, pk=None):
        """
        Mark all pending refill requests for this fixture as completed.
        Requires authentication.
        """
        fixture = self.get_object()

        # Get the user who is resolving
        resolved_by = request.user.username if request.user.is_authenticated else ""
        notes = request.data.get("notes", "")

        # Update all pending requests
        from django.utils import timezone

        updated_count = FixtureRefillRequest.objects.filter(
            fixture=fixture, status="pending"
        ).update(
            status="completed",
            resolved_at=timezone.now(),
            resolved_by=resolved_by,
            # Only update notes if provided
            notes=notes if notes else F("notes"),
        )

        return Response(
            {
                "message": f"Resolved {updated_count} pending refill request(s)",
                "fixture": FixtureDetailSerializer(fixture).data,
            }
        )


class FixtureRefillRequestViewSet(viewsets.ModelViewSet):
    """API endpoint for fixture refill requests."""

    queryset = FixtureRefillRequest.objects.select_related(
        "fixture", "fixture__location", "fixture__refill_item"
    ).all()
    serializer_class = FixtureRefillRequestSerializer

    def get_permissions(self):
        """Allow anyone to create requests (scan QR codes), but require auth for other operations."""
        if self.action == "create":
            return [AllowAny()]
        return [IsAuthenticatedOrReadOnly()]

    def get_queryset(self):
        queryset = super().get_queryset()

        # Filter by fixture if specified
        fixture = self.request.query_params.get("fixture")
        if fixture:
            queryset = queryset.filter(fixture_id=fixture)

        # Filter by status if specified
        request_status = self.request.query_params.get("status")
        if request_status:
            queryset = queryset.filter(status=request_status)

        # Filter by location if specified
        location = self.request.query_params.get("location")
        if location:
            queryset = queryset.filter(fixture__location_id=location)

        return queryset.order_by("-requested_at")

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        """Mark a single refill request as completed."""
        refill_request = self.get_object()

        if refill_request.status == "completed":
            return Response(
                {"error": "This request is already completed"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from django.utils import timezone

        refill_request.status = "completed"
        refill_request.resolved_at = timezone.now()
        refill_request.resolved_by = request.user.username if request.user.is_authenticated else ""
        refill_request.notes = request.data.get("notes", refill_request.notes)
        refill_request.save()

        serializer = self.get_serializer(refill_request)
        return Response(serializer.data)


@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def lookup_by_code(request):
    """
    Look up an asset, inventory item, or location by access code.

    Accepts GET or POST with 'code' parameter.
    Returns the appropriate item with its type and redirect URL.
    """
    code = request.data.get("code") or request.query_params.get("code", "").strip().upper()

    if not code:
        return Response(
            {"error": "Code parameter is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Validate code format (6 characters, alphanumeric, excluding I, 0, O, 1, L)
    if len(code) != 6:
        return Response(
            {"error": "Code must be exactly 6 characters"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Check for invalid characters
    invalid_chars = set(code) - set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
    if invalid_chars:
        return Response(
            {"error": f"Code contains invalid characters: {', '.join(invalid_chars)}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Try to find in each model
    from django.conf import settings

    frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")

    # Check Asset
    try:
        asset = Asset.objects.get(access_code=code)
        # Log the scan (same as QR code scanning) - update last_scanned_at
        from django.utils import timezone

        asset.last_scanned_at = timezone.now()
        asset.save(update_fields=["last_scanned_at"])

        return Response(
            {
                "type": "asset",
                "id": str(asset.id),
                "name": asset.name,
                "url": f"{frontend_url}/scan/asset/{asset.id}",
            }
        )
    except Asset.DoesNotExist:
        pass

    # Check InventoryItem
    try:
        item = InventoryItem.objects.get(access_code=code)
        # Log the scan (same as QR code scanning) - update last_scanned_at
        from django.utils import timezone

        item.last_scanned_at = timezone.now()
        item.save(update_fields=["last_scanned_at"])

        return Response(
            {
                "type": "item",
                "id": str(item.id),
                "name": item.name,
                "url": f"{frontend_url}/scan/{item.id}",
            }
        )
    except InventoryItem.DoesNotExist:
        pass

    # Check Location
    try:
        location = Location.objects.get(access_code=code)
        # Locations don't have scan logging currently, but the scan page handles it

        return Response(
            {
                "type": "location",
                "id": str(location.id),
                "name": location.name,
                "url": f"{frontend_url}/scan/location/{location.id}",
            }
        )
    except Location.DoesNotExist:
        pass

    # Not found
    return Response(
        {"error": "Code not found"},
        status=status.HTTP_404_NOT_FOUND,
    )


class InventoryReportViewSet(viewsets.ViewSet):
    """API endpoint for inventory reports."""

    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["get"])
    def stock_by_category(self, request):
        """Get stock levels aggregated by category."""
        from django.db.models import Count, OuterRef, Q, Subquery, Sum, Value
        from django.db.models.functions import Coalesce

        from inventory.models import ItemSupplier

        # Subquery to get unit_cost from primary ItemSupplier
        primary_supplier = ItemSupplier.objects.filter(
            item=OuterRef("pk"), is_primary=True, is_active=True, unit_cost__isnull=False
        ).order_by("unit_cost")[:1]

        queryset = (
            InventoryItem.objects.filter(is_active=True)
            .select_related("category")
            .annotate(
                category_id_coalesced=Coalesce("category__id", Value(0)),
                category_name_coalesced=Coalesce("category__name", Value("Uncategorized")),
                unit_cost_value=Subquery(primary_supplier.values("unit_cost")),
            )
            .values("category_id_coalesced", "category_name_coalesced")
            .annotate(
                total_items=Count("id"),
                total_stock=Sum("current_stock"),
                total_value=Sum(
                    F("current_stock") * Coalesce("unit_cost_value", Value(0)),
                    output_field=models.DecimalField(max_digits=20, decimal_places=2),
                ),
                low_stock_count=Count("id", filter=Q(current_stock__lte=F("minimum_stock"))),
            )
            .order_by("category_name_coalesced")
        )

        data = []
        for item in queryset:
            data.append(
                {
                    "category_id": (
                        item["category_id_coalesced"]
                        if item["category_id_coalesced"] != 0
                        else None
                    ),
                    "category_name": item["category_name_coalesced"],
                    "total_items": item["total_items"],
                    "total_stock": item["total_stock"] or 0,
                    "total_value": float(item["total_value"] or 0),
                    "low_stock_count": item["low_stock_count"],
                }
            )

        return Response(data)

    @action(detail=False, methods=["get"])
    def reorder_frequency(self, request):
        """Get reorder frequency per item with time period filter."""
        from datetime import timedelta

        from django.db.models import Count

        from reorder_queue.models import ReorderRequest

        # Get time period from query params (default: last 12 months)
        months = int(request.query_params.get("months", 12))
        start_date = timezone.now() - timedelta(days=months * 30)

        # Get reorder requests in the time period
        reorder_requests = (
            ReorderRequest.objects.filter(requested_at__gte=start_date)
            .select_related("item", "item__category")
            .values("item__id", "item__name", "item__sku", "item__category__name")
            .annotate(reorder_count=Count("id"))
            .order_by("-reorder_count")
        )

        data = []
        for req in reorder_requests:
            data.append(
                {
                    "item_id": str(req["item__id"]),
                    "item_name": req["item__name"],
                    "item_sku": req["item__sku"] or "",
                    "category_name": req["item__category__name"] or "Uncategorized",
                    "reorder_count": req["reorder_count"],
                }
            )

        return Response(data)

    @action(detail=False, methods=["get"])
    def value_by_location(self, request):
        """Get total inventory value grouped by location."""
        from django.db.models import Count, OuterRef, Subquery, Sum, Value
        from django.db.models.functions import Coalesce

        from inventory.models import ItemSupplier

        # Subquery to get unit_cost from primary ItemSupplier
        primary_supplier = ItemSupplier.objects.filter(
            item=OuterRef("pk"), is_primary=True, is_active=True, unit_cost__isnull=False
        ).order_by("unit_cost")[:1]

        queryset = (
            InventoryItem.objects.filter(is_active=True)
            .select_related("location")
            .annotate(
                location_id_coalesced=Coalesce("location__id", Value(0)),
                location_name_coalesced=Coalesce("location__name", Value("No Location")),
                unit_cost_value=Subquery(primary_supplier.values("unit_cost")),
            )
            .values("location_id_coalesced", "location_name_coalesced")
            .annotate(
                total_items=Count("id"),
                total_stock=Sum("current_stock"),
                total_value=Sum(
                    F("current_stock") * Coalesce("unit_cost_value", Value(0)),
                    output_field=models.DecimalField(max_digits=20, decimal_places=2),
                ),
            )
            .order_by("-total_value")
        )

        data = []
        for item in queryset:
            data.append(
                {
                    "location_id": (
                        item["location_id_coalesced"]
                        if item["location_id_coalesced"] != 0
                        else None
                    ),
                    "location_name": item["location_name_coalesced"],
                    "total_items": item["total_items"],
                    "total_stock": item["total_stock"] or 0,
                    "total_value": float(item["total_value"] or 0),
                }
            )

        return Response(data)

    @action(detail=False, methods=["get"])
    def export(self, request):
        """Export inventory report data as CSV."""
        import csv

        report_type = request.query_params.get("type", "stock_by_category")

        if report_type == "stock_by_category":
            response = self.stock_by_category(request)
            data = response.data

            response_obj = HttpResponse(content_type="text/csv")
            response_obj["Content-Disposition"] = (
                'attachment; filename="inventory_stock_by_category.csv"'
            )

            writer = csv.DictWriter(
                response_obj,
                fieldnames=[
                    "category_name",
                    "total_items",
                    "total_stock",
                    "total_value",
                    "low_stock_count",
                ],
            )
            writer.writeheader()
            for row in data:
                writer.writerow(
                    {
                        "category_name": row["category_name"],
                        "total_items": row["total_items"],
                        "total_stock": row["total_stock"],
                        "total_value": f"{row['total_value']:.2f}",
                        "low_stock_count": row["low_stock_count"],
                    }
                )

            return response_obj
        elif report_type == "reorder_frequency":
            response = self.reorder_frequency(request)
            data = response.data

            response_obj = HttpResponse(content_type="text/csv")
            response_obj["Content-Disposition"] = (
                'attachment; filename="inventory_reorder_frequency.csv"'
            )

            writer = csv.DictWriter(
                response_obj,
                fieldnames=["item_name", "item_sku", "category_name", "reorder_count"],
            )
            writer.writeheader()
            for row in data:
                writer.writerow(
                    {
                        "item_name": row["item_name"],
                        "item_sku": row["item_sku"],
                        "category_name": row["category_name"],
                        "reorder_count": row["reorder_count"],
                    }
                )

            return response_obj
        elif report_type == "value_by_location":
            response = self.value_by_location(request)
            data = response.data

            response_obj = HttpResponse(content_type="text/csv")
            response_obj["Content-Disposition"] = (
                'attachment; filename="inventory_value_by_location.csv"'
            )

            writer = csv.DictWriter(
                response_obj,
                fieldnames=["location_name", "total_items", "total_stock", "total_value"],
            )
            writer.writeheader()
            for row in data:
                writer.writerow(
                    {
                        "location_name": row["location_name"],
                        "total_items": row["total_items"],
                        "total_stock": row["total_stock"],
                        "total_value": f"{row['total_value']:.2f}",
                    }
                )

            return response_obj

        return Response({"error": "Invalid report type"}, status=status.HTTP_400_BAD_REQUEST)


class AssetReportViewSet(viewsets.ViewSet):
    """API endpoint for asset reports."""

    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["get"])
    def assets_by_status(self, request):
        """Get count of assets grouped by status."""
        from django.db.models import Count

        queryset = Asset.objects.values("status").annotate(count=Count("id")).order_by("status")

        data = []
        status_choices = dict(Asset.STATUS_CHOICES)
        for item in queryset:
            data.append(
                {
                    "status": item["status"],
                    "status_display": status_choices.get(item["status"], item["status"]),
                    "count": item["count"],
                }
            )

        return Response(data)

    @action(detail=False, methods=["get"])
    def maintenance_due(self, request):
        """Get assets and parts that need maintenance."""
        # Get assets with parts that need maintenance
        maintenance_due_parts = AssetPart.objects.filter(
            maintenance_interval_days__isnull=False,
            last_replaced_at__isnull=False,
        ).select_related("asset", "part")

        maintenance_needed = []

        for part in maintenance_due_parts:
            if part.needs_replacement:
                days_since = part.days_since_replacement or 0
                days_overdue = days_since - (part.maintenance_interval_days or 0)

                maintenance_needed.append(
                    {
                        "asset_id": str(part.asset.id),
                        "asset_name": part.asset.name,
                        "asset_tag": part.asset.asset_tag or "",
                        "part_id": str(part.part.id),
                        "part_name": part.part.name,
                        "part_sku": part.part.sku or "",
                        "maintenance_interval_days": part.maintenance_interval_days,
                        "days_since_replacement": days_since,
                        "days_overdue": max(0, days_overdue),
                        "last_replaced_at": (
                            part.last_replaced_at.isoformat() if part.last_replaced_at else None
                        ),
                    }
                )

        # Also check assets that are in maintenance status
        assets_in_maintenance = Asset.objects.filter(status=Asset.MAINTENANCE).select_related(
            "category", "location"
        )

        for asset in assets_in_maintenance:
            maintenance_needed.append(
                {
                    "asset_id": str(asset.id),
                    "asset_name": asset.name,
                    "asset_tag": asset.asset_tag or "",
                    "part_id": None,
                    "part_name": None,
                    "part_sku": None,
                    "maintenance_interval_days": None,
                    "days_since_replacement": None,
                    "days_overdue": None,
                    "last_replaced_at": None,
                    "status": "in_maintenance",
                }
            )

        # Sort by days overdue (most urgent first)
        maintenance_needed.sort(key=lambda x: x.get("days_overdue") or 0, reverse=True)

        return Response(maintenance_needed)

    @action(detail=False, methods=["get"])
    def utilization(self, request):
        """Get asset utilization statistics from DeviceUsage."""
        from datetime import timedelta

        from django.db.models import Avg, Count, Sum

        try:
            from forgekey.models import DeviceUsage

            # Get time period from query params (default: last 30 days)
            days = int(request.query_params.get("days", 30))
            start_date = timezone.now() - timedelta(days=days)

            # Get usage statistics per asset
            usage_stats = (
                DeviceUsage.objects.filter(started_at__gte=start_date)
                .select_related("asset")
                .values("asset__id", "asset__name", "asset__asset_tag")
                .annotate(
                    total_sessions=Count("id"),
                    total_duration_seconds=Sum("duration_seconds"),
                    avg_duration_seconds=Avg("duration_seconds"),
                )
                .order_by("-total_duration_seconds")
            )

            data = []
            for stat in usage_stats:
                total_hours = (stat["total_duration_seconds"] or 0) / 3600
                avg_hours = (stat["avg_duration_seconds"] or 0) / 3600

                data.append(
                    {
                        "asset_id": str(stat["asset__id"]),
                        "asset_name": stat["asset__name"],
                        "asset_tag": stat["asset__asset_tag"] or "",
                        "total_sessions": stat["total_sessions"],
                        "total_hours": round(total_hours, 2),
                        "avg_hours_per_session": round(avg_hours, 2),
                    }
                )

            return Response(data)
        except ImportError:
            return Response(
                {"error": "DeviceUsage model not available"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

    @action(detail=False, methods=["get"])
    def export(self, request):
        """Export asset report data as CSV."""
        import csv

        report_type = request.query_params.get("type", "assets_by_status")

        if report_type == "assets_by_status":
            response = self.assets_by_status(request)
            data = response.data

            response_obj = HttpResponse(content_type="text/csv")
            response_obj["Content-Disposition"] = 'attachment; filename="assets_by_status.csv"'

            writer = csv.DictWriter(
                response_obj,
                fieldnames=["status", "status_display", "count"],
            )
            writer.writeheader()
            for row in data:
                writer.writerow(row)

            return response_obj
        elif report_type == "maintenance_due":
            response = self.maintenance_due(request)
            data = response.data

            response_obj = HttpResponse(content_type="text/csv")
            response_obj["Content-Disposition"] = (
                'attachment; filename="assets_maintenance_due.csv"'
            )

            writer = csv.DictWriter(
                response_obj,
                fieldnames=[
                    "asset_name",
                    "asset_tag",
                    "part_name",
                    "part_sku",
                    "maintenance_interval_days",
                    "days_since_replacement",
                    "days_overdue",
                    "last_replaced_at",
                ],
            )
            writer.writeheader()
            for row in data:
                writer.writerow(
                    {
                        "asset_name": row["asset_name"],
                        "asset_tag": row["asset_tag"],
                        "part_name": row["part_name"] or "",
                        "part_sku": row["part_sku"] or "",
                        "maintenance_interval_days": row["maintenance_interval_days"] or "",
                        "days_since_replacement": row["days_since_replacement"] or "",
                        "days_overdue": row["days_overdue"] or 0,
                        "last_replaced_at": row["last_replaced_at"] or "",
                    }
                )

            return response_obj
        elif report_type == "utilization":
            response = self.utilization(request)
            data = response.data

            response_obj = HttpResponse(content_type="text/csv")
            response_obj["Content-Disposition"] = 'attachment; filename="assets_utilization.csv"'

            writer = csv.DictWriter(
                response_obj,
                fieldnames=[
                    "asset_name",
                    "asset_tag",
                    "total_sessions",
                    "total_hours",
                    "avg_hours_per_session",
                ],
            )
            writer.writeheader()
            for row in data:
                writer.writerow(
                    {
                        "asset_name": row["asset_name"],
                        "asset_tag": row["asset_tag"],
                        "total_sessions": row["total_sessions"],
                        "total_hours": row["total_hours"],
                        "avg_hours_per_session": row["avg_hours_per_session"],
                    }
                )

            return response_obj

        return Response({"error": "Invalid report type"}, status=status.HTTP_400_BAD_REQUEST)
