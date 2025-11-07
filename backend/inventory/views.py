"""
Views for inventory API.
"""

from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import F, Q
from django.http import HttpResponse

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.response import Response

from .models import (
    Asset,
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
    AssetSerializer,
    CategorySerializer,
    FixtureDetailSerializer,
    FixtureRefillRequestSerializer,
    FixtureSerializer,
    InventoryItemDetailSerializer,
    InventoryItemSerializer,
    ItemSupplierSerializer,
    PriceHistorySerializer,
    SupplierSerializer,
    UsageLogSerializer,
)


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
        if self.action in [
            "list",
            "retrieve",
            "low_stock",
            "reordered",
            "download_card",
            "log_usage",
            "generate_qr",
            "qr_code",
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

    @action(detail=True, methods=["get"], url_path="qr_code", url_name="qr_code", name="QR Code")
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

    queryset = Asset.objects.select_related(
        "inventory_item", "category", "location", "manufacturer"
    ).all()
    serializer_class = AssetSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        queryset = super().get_queryset()

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

        return queryset.order_by("name")

    @action(detail=True, methods=["post"])
    def generate_qr(self, request, pk=None):
        """Generate QR code for an asset."""
        asset = self.get_object()

        # Generate QR code synchronously for immediate response
        from .utils.qr_generator import save_qr_code_to_asset

        try:
            save_qr_code_to_asset(asset)
            serializer = self.get_serializer(asset)
            return Response(serializer.data)
        except Exception as e:
            return Response(
                {"error": f"Failed to generate QR code: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

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
            .select_related("category", "location", "manufacturer")
        )

        serializer = self.get_serializer(assets, many=True)
        return Response(serializer.data)

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

    @action(detail=True, methods=["post"])
    def enable(self, request, pk=None):
        """Enable an asset (set is_active=True)."""
        asset = self.get_object()
        user = request.user

        if not user.is_authenticated:
            return Response(
                {"error": "Authentication required"}, status=status.HTTP_401_UNAUTHORIZED
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
        asset.is_active = False
        asset.save(update_fields=["is_active"])
        serializer = self.get_serializer(asset)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def lock(self, request, pk=None):
        """Lock an asset to prevent non-admin usage."""
        from django.utils import timezone

        asset = self.get_object()
        user = request.user

        if not user.is_authenticated:
            return Response(
                {"error": "Authentication required"}, status=status.HTTP_401_UNAUTHORIZED
            )

        # Check if user can lock this asset
        if not asset.can_user_lock(user):
            return Response(
                {"error": "You do not have permission to lock this asset"},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Determine lock type based on user's role
        lock_type = None
        if asset.is_user_admin(user):
            lock_type = asset.LOCK_TYPE_ADMIN
        elif asset.is_user_in_logistics(user):
            lock_type = asset.LOCK_TYPE_LOGISTICS
        elif asset.is_user_group_admin(user):
            lock_type = asset.LOCK_TYPE_GROUP_ADMIN
        elif asset.owning_group and asset.owning_group in user.groups.all():
            lock_type = asset.LOCK_TYPE_GROUP_MEMBER

        if not lock_type:
            return Response(
                {"error": "Unable to determine lock type"}, status=status.HTTP_400_BAD_REQUEST
            )

        # Lock the asset
        asset.is_locked = True
        asset.locked_by = user
        asset.locked_at = timezone.now()
        asset.lock_type = lock_type
        asset.save(update_fields=["is_locked", "locked_by", "locked_at", "lock_type"])

        serializer = self.get_serializer(asset)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def unlock(self, request, pk=None):
        """Unlock an asset based on permission rules."""
        asset = self.get_object()
        user = request.user

        if not user.is_authenticated:
            return Response(
                {"error": "Authentication required"}, status=status.HTTP_401_UNAUTHORIZED
            )

        # Check if user can unlock this asset
        if not asset.can_user_unlock(user):
            return Response(
                {"error": "You do not have permission to unlock this asset"},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Unlock the asset
        asset.is_locked = False
        asset.locked_by = None
        asset.locked_at = None
        asset.lock_type = ""
        asset.save(update_fields=["is_locked", "locked_by", "locked_at", "lock_type"])

        serializer = self.get_serializer(asset)
        return Response(serializer.data)

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
                {"error": "This fixture is inactive"}, status=status.HTTP_400_BAD_REQUEST
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
        except Exception as e:
            # Log but don't fail the request if webhook fails
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
            notes=notes if notes else F("notes"),  # Only update notes if provided
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
