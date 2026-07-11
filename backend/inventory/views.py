"""
Views for inventory API.
"""

import csv
import io
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models, transaction
from django.db.models import F, Q
from django.http import HttpResponse, StreamingHttpResponse
from django.utils import timezone

from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import (
    AllowAny,
    IsAdminUser,
    IsAuthenticated,
    IsAuthenticatedOrReadOnly,
)
from rest_framework.response import Response

from config.api_errors import ErrorCode, error_response
from membership.permissions import IsAuthenticatedOrStaffSigAdminWrite, IsStaffOrSigAdmin

from .audit import record_event as record_maintenance_audit_event
from .models import (
    Asset,
    AssetDocument,
    AssetMeter,
    AssetMeterReading,
    AssetOutOfService,
    AssetPart,
    AssetProblem,
    AssetReservation,
    Category,
    ComponentUsageEvent,
    Fixture,
    FixtureRefillRequest,
    InventoryItem,
    ItemSupplier,
    Location,
    LocationProblem,
    MaintenanceItem,
    MaintenanceLog,
    MaintenanceMaterial,
    MaintenanceRecord,
    MaintenanceTask,
    PriceHistory,
    SerializedComponent,
    StockReconciliation,
    Supplier,
    UsageLog,
    WorkOrder,
    WorkOrderMaterialUsage,
    WorkOrderSubmission,
    WorkOrderTaskCompletion,
    WorkOrderValidation,
)
from .serializers import (
    AssetDocumentSerializer,
    AssetMeterReadingSerializer,
    AssetMeterSerializer,
    AssetOutOfServiceSerializer,
    AssetPartSerializer,
    AssetProblemPhotoSerializer,
    AssetProblemSerializer,
    AssetReservationSerializer,
    AssetSerializer,
    CategorySerializer,
    ComponentUsageEventSerializer,
    FixtureDetailSerializer,
    FixtureRefillRequestSerializer,
    FixtureSerializer,
    InventoryItemDetailSerializer,
    InventoryItemSerializer,
    InventoryMetricsSerializer,
    ItemSupplierSerializer,
    LocationProblemSerializer,
    LocationReconcileItemSerializer,
    LocationSerializer,
    MaintenanceItemSerializer,
    MaintenanceLogSerializer,
    MaintenanceMaterialSerializer,
    MaintenanceRecordSerializer,
    MaintenanceTaskSerializer,
    PriceHistorySerializer,
    SerializedComponentSerializer,
    StockReconciliationBatchSerializer,
    StockReconciliationSerializer,
    SupplierDetailSerializer,
    SupplierSerializer,
    UsageLogSerializer,
    WorkOrderListSerializer,
    WorkOrderMaterialUsageSerializer,
    WorkOrderPhotoSerializer,
    WorkOrderSerializer,
    WorkOrderTaskCompletionSerializer,
    WorkOrderValidationSerializer,
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
            return error_response(
                ErrorCode.NOT_FOUND,
                "QR code not generated yet",
                status_code=status.HTTP_404_NOT_FOUND,
            )

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

    @action(
        detail=True,
        methods=["post"],
        permission_classes=[AllowAny],
        parser_classes=[MultiPartParser, FormParser],
    )
    def report_problem(self, request, pk=None):
        """Report a problem at this location.

        Mirrors Asset.report_problem. Accepts ``description``, ``severity``,
        and an optional ``photo`` upload. Anonymous reporters are allowed
        (parallel to QR-scan asset reporting); authenticated users get their
        username recorded.
        """
        location = self.get_object()
        description = (request.data.get("description") or "").strip()
        if not description:
            return Response(
                {"error": "description is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        severity = request.data.get("severity") or LocationProblem.SEVERITY_MEDIUM
        valid_sev = {choice for choice, _ in LocationProblem.SEVERITY_CHOICES}
        if severity not in valid_sev:
            return Response(
                {"error": f"severity must be one of {sorted(valid_sev)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reported_by = ""
        if request.user and request.user.is_authenticated:
            reported_by = request.user.username

        problem = LocationProblem.objects.create(
            location=location,
            reported_by=reported_by,
            description=description,
            severity=severity,
            photo=request.FILES.get("photo"),
        )

        try:
            from notifications.services import notify_admins

            reporter_text = f" by {reported_by}" if reported_by else ""
            notify_admins(
                type="warning",
                title=f"Location problem reported: {location.name}",
                message=(
                    f"A problem was reported{reporter_text} at {location.name} "
                    f"[{problem.get_severity_display()}]: {description[:200]}"
                ),
                action_url=f"/inventory/locations/{location.id}",
                metadata={
                    "location_problem_id": str(problem.id),
                    "location_id": str(location.id),
                    "severity": problem.severity,
                },
            )
        except Exception:  # nosec B110 - notifications must not block reports
            pass

        # Webhook + email fan-out for the new oms-0yz logistics flow. Both must
        # fail open: the user-facing report should never 500 because Celery,
        # Postmark, or a downstream subscriber is unavailable.
        try:
            from reorder_queue.tasks import send_location_problem_webhook

            send_location_problem_webhook.delay(str(problem.id))
        except Exception:  # nosec B110 - webhook delivery must not block reports
            pass

        if problem.severity in (
            LocationProblem.SEVERITY_HIGH,
            LocationProblem.SEVERITY_URGENT,
        ):
            try:
                from inventory.services.location_problem_alerts import email_logistics_alert

                email_logistics_alert(problem)
            except Exception:  # nosec B110 - email delivery must not block reports
                pass

        serializer = LocationProblemSerializer(problem, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], permission_classes=[AllowAny])
    def problems(self, request, pk=None):
        """List problem reports filed against this location."""
        location = self.get_object()
        qs = LocationProblem.objects.filter(location=location).order_by("-reported_at")
        serializer = LocationProblemSerializer(qs, many=True, context={"request": request})
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
            "metrics",
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

        # Filter by low stock if specified. Retired items are phased out and
        # must never surface in a low-stock filter, even when explicitly empty.
        low_stock = self.request.query_params.get("low_stock", "").lower()
        if low_stock == "true":
            queryset = queryset.filter(current_stock__lte=F("minimum_stock")).exclude(
                is_retired=True
            )
        elif low_stock == "false":
            queryset = queryset.filter(current_stock__gt=F("minimum_stock"))

        # Filter by active status if specified
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == "true")

        # Retired visibility (Ian's decision): a retired item stays listed so its
        # remaining stock is drawn down, but is auto-hidden once stock reaches 0.
        # Retired items with stock > 0 are always shown. ``include_retired=true``
        # opts out of the auto-hide so retired-and-empty items are reachable too.
        # This is the single visibility chokepoint the low_stock/reordered
        # actions reuse.
        include_retired = self.request.query_params.get("include_retired", "").lower()
        if include_retired != "true":
            queryset = queryset.exclude(is_retired=True, current_stock__lte=0)

        # Ordering support (validated against an allow-list to keep the
        # client-driven `ordering` param from reaching arbitrary fields).
        ordering = self.request.query_params.get("ordering", "name")
        valid_ordering_fields = {
            "name",
            "-name",
            "sku",
            "-sku",
            "current_stock",
            "-current_stock",
            "minimum_stock",
            "-minimum_stock",
            "category__name",
            "-category__name",
            "location__name",
            "-location__name",
            "created_at",
            "-created_at",
            "updated_at",
            "-updated_at",
        }
        if ordering in valid_ordering_fields:
            return queryset.order_by(ordering)
        return queryset.order_by("name")

    @staticmethod
    def _wants_metrics(request):
        """Whether the opt-in ``?with_metrics`` list annotation was requested."""
        value = request.query_params.get("with_metrics")
        return value is not None and str(value).strip().lower() in {"1", "true", "yes", "on"}

    def list(self, request, *args, **kwargs):
        """List inventory items, optionally annotated with computed metrics.

        With ``?with_metrics=1`` each returned item gains a ``metrics`` object
        (the same shape as the ``/metrics/`` detail action). Metrics are
        computed AFTER pagination — for the page's items only, never the whole
        table — in a bounded number of queries, so the annotation cannot become
        an N+1. Without the param the response and its query count are
        unchanged.
        """
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        items = page if page is not None else list(queryset)

        serializer = self.get_serializer(items, many=True)
        data = serializer.data
        if self._wants_metrics(request):
            self._annotate_metrics(data, items)

        if page is not None:
            return self.get_paginated_response(data)
        return Response(data)

    @staticmethod
    def _annotate_metrics(data, items):
        """Attach a ``metrics`` object to each serialized row for ``items``.

        ``data`` and ``items`` are positionally aligned (the serializer keeps
        input order), so rows are matched to items by index rather than by id —
        robust regardless of the item PK type.
        """
        from .services.item_metrics import compute_item_metrics_batch

        metrics_by_id = compute_item_metrics_batch(items)
        for row, item in zip(data, items):
            payload = metrics_by_id.get(item.id)
            row["metrics"] = (
                InventoryMetricsSerializer(payload).data if payload is not None else None
            )

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
            return error_response(
                ErrorCode.SERVER_ERROR,
                str(e),
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

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
            return error_response(
                ErrorCode.NOT_FOUND,
                "QR code not generated yet",
                status_code=status.HTTP_404_NOT_FOUND,
            )

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
    def retire(self, request, pk=None):
        """Retire (phase out) this item.

        A retired item is never flagged for reorder and is auto-hidden from the
        default list once its stock hits 0 (retired items with stock remaining
        stay listed so the remaining stock is drawn down). Idempotent: retiring
        an already-retired item preserves the original ``retired_at`` stamp.
        """
        item = self.get_object()
        if not item.is_retired:
            item.is_retired = True
            item.retired_at = timezone.now()
            item.save(update_fields=["is_retired", "retired_at", "updated_at"])
        serializer = self.get_serializer(item)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def unretire(self, request, pk=None):
        """Un-retire this item, returning it to normal reorder/list behavior."""
        item = self.get_object()
        if item.is_retired:
            item.is_retired = False
            item.retired_at = None
            item.save(update_fields=["is_retired", "retired_at", "updated_at"])
        serializer = self.get_serializer(item)
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

    @action(detail=True, methods=["post"], url_path="cycle-count")
    def cycle_count(self, request, pk=None):
        """Record a physical cycle count for this inventory item.

        Reconciles system on-hand to the counted quantity via the shared
        ``_apply_reconciliation_row`` helper — it writes the StockReconciliation
        audit row, sets ``current_stock`` to the actual count, and auto-creates a
        ReorderRequest when at/below minimum (unless ``skip_reorder``). Afterwards
        the item's ``last_counted_at`` is stamped so the detail views can show
        days-since-last-count.

        Body: ``counted_qty`` (required int >= 0), ``reason`` (required, from
        ``StockReconciliation.REASON_CHOICES``), ``skip_reorder`` (optional bool),
        ``notes`` (optional str).
        """
        item = self.get_object()

        if not _user_can_reconcile_item(request.user, item):
            return Response(
                {"detail": "You do not have permission to cycle-count this item."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            counted_qty = int(request.data.get("counted_qty"))
        except (TypeError, ValueError):
            return Response(
                {"detail": "counted_qty is required and must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if counted_qty < 0:
            return Response(
                {"detail": "counted_qty must be >= 0."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reason = request.data.get("reason")
        valid_reasons = {v for v, _ in StockReconciliation.REASON_CHOICES}
        if reason not in valid_reasons:
            return Response(
                {"detail": ("reason is required; choose from " f"{sorted(valid_reasons)}.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        skip_reorder = bool(request.data.get("skip_reorder", False))
        notes = request.data.get("notes", "") or ""

        with transaction.atomic():
            reconciliation, _reorder_created = _apply_reconciliation_row(
                request.user,
                item,
                counted_qty,
                reason,
                notes=notes,
                skip_reorder=skip_reorder,
            )
            item.last_counted_at = timezone.now()
            item.save(update_fields=["last_counted_at"])

        days_since_last_count = (timezone.now() - item.last_counted_at).days

        return Response(
            {
                "id": str(item.id),
                "current_stock": item.current_stock,
                "last_counted_at": item.last_counted_at,
                "days_since_last_count": days_since_last_count,
                "reconciliation": {
                    "id": reconciliation.id,
                    "projected_count": reconciliation.projected_count,
                    "actual_count": reconciliation.actual_count,
                    "delta": reconciliation.delta,
                    "reason": reconciliation.reason,
                    "reconciled_at": reconciliation.reconciled_at,
                    "reconciled_by": reconciliation.reconciled_by_id,
                },
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["get"], permission_classes=[AllowAny])
    def metrics(self, request, pk=None):
        """Computed stock + cost metrics for the item detail view (issue-5).

        Powers the ``SKU · QOH · QOO · QA · QC · QIT · RP · Lead · Cost`` row on
        the web item-detail page and the paired ScanTTY TUI row. The field
        names are a pinned contract shared with the ScanTTY worker — see
        ``InventoryMetricsSerializer``. Read-only; no migration.

        The computation is shared with the ``?with_metrics=1`` list annotation
        via ``compute_item_metrics`` (see ``services/item_metrics.py``).
        """
        from .services.item_metrics import compute_item_metrics

        item = self.get_object()
        payload = compute_item_metrics(item)
        return Response(InventoryMetricsSerializer(payload).data)

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
        Asset.objects.select_related(
            "inventory_item",
            "category",
            "location",
            "manufacturer",
            # breaker/disconnect moved to the 1:1 site_requirements profile (#880);
            # traverse it so breaker_summary/disconnect_summary stay N+0.
            "site_requirements__breaker__panel",
            "site_requirements__disconnect",
        )
        .prefetch_related(
            "asset_parts__part",
            "asset_parts__part__category",
            "required_certifications__sig",
            # Nested `meters` on AssetSerializer — prefetch so the asset list
            # stays bounded (see test_asset_list_is_bounded).
            "meters",
        )
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
    def tag(self, request, pk=None):
        """Render an asset tag PNG suitable for riveting onto the asset."""
        from membership.utils import can_manage_sig_asset

        from .services.asset_tag_service import SIZES, InvalidTagSizeError, render_asset_tag

        asset = self.get_object()

        if not can_manage_sig_asset(request.user, asset):
            return Response(
                {"detail": "You do not have permission to view this asset's tag."},
                status=status.HTTP_403_FORBIDDEN,
            )

        size = request.query_params.get("size", "standard")
        if size not in SIZES:
            return Response(
                {"detail": f"Invalid size '{size}'. Valid sizes: {sorted(SIZES)}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            png_bytes = render_asset_tag(asset, size=size)
        except InvalidTagSizeError as exc:
            return error_response(ErrorCode.VALIDATION_FAILED, str(exc))

        response = HttpResponse(png_bytes, content_type="image/png")
        if request.query_params.get("download") == "1":
            response["Content-Disposition"] = (
                f'attachment; filename="asset-tag-{asset.id}-{size}.png"'
            )
        return response

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
                return error_response(
                    ErrorCode.NOT_FOUND,
                    "No assets found",
                    status_code=status.HTTP_404_NOT_FOUND,
                )

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
    def maintenance_items(self, request, pk=None):
        """Get active maintenance items for this asset, ordered by urgency."""
        asset = self.get_object()
        items = MaintenanceItem.objects.filter(asset=asset, is_active=True).prefetch_related(
            "materials"
        )
        serializer = MaintenanceItemSerializer(items, many=True)
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
            return error_response(ErrorCode.VALIDATION_FAILED, "reason is required")

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
            return error_response(ErrorCode.VALIDATION_FAILED, "Asset is not locked")

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

        from .models import AssetPart, AssetProblem

        # Optional multi-select: which of the asset's parts need replace/fix.
        # Every id must be an AssetPart belonging to THIS asset; anything else
        # (unknown id, or a part on a different asset) is a client error.
        if hasattr(request.data, "getlist"):
            # QueryDict (form/multipart) — collect repeated part_ids keys.
            part_ids = request.data.getlist("part_ids") or None
        else:
            part_ids = request.data.get("part_ids")

        valid_parts = []
        if part_ids:
            if not isinstance(part_ids, (list, tuple)):
                return Response(
                    {"error": "part_ids must be a list of AssetPart ids"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                requested_ids = [int(pid) for pid in part_ids]
            except (TypeError, ValueError):
                return Response(
                    {"error": "part_ids must be a list of integer AssetPart ids"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            valid_parts = list(AssetPart.objects.filter(asset=asset, id__in=requested_ids))
            found_ids = {p.id for p in valid_parts}
            invalid = sorted({pid for pid in requested_ids if pid not in found_ids})
            if invalid:
                return Response(
                    {
                        "error": (
                            "part_ids must reference AssetParts belonging to this "
                            f"asset; invalid: {invalid}"
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        reported_by = ""
        if request.user and request.user.is_authenticated:
            reported_by = request.user.username

        problem = AssetProblem.objects.create(
            asset=asset,
            reported_by=reported_by,
            description=description,
        )
        if valid_parts:
            problem.affected_parts.set(valid_parts)

        # Send webhook notification if configured
        try:
            from reorder_queue.tasks import send_asset_problem_webhook

            send_asset_problem_webhook.delay(str(problem.id))
        except Exception as e:
            # Log but don't fail the request if webhook fails
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to send asset problem webhook: {e}", exc_info=True)

        # Create in-app notifications for admins about the asset problem
        try:
            from notifications.services import notify_admins

            reporter_text = f" by {reported_by}" if reported_by else ""
            notify_admins(
                type="warning",
                title=f"Asset problem reported: {asset.name}",
                message=(
                    f"A problem was reported{reporter_text} for {asset.name}: "
                    f"{description[:200]}"
                ),
                action_url=f"/inventory/assets/{asset.id}",
                metadata={
                    "asset_problem_id": str(problem.id),
                    "asset_id": str(asset.id),
                },
            )
        except Exception:  # nosec B110
            # Don't fail the request if notification creation fails
            pass

        from .serializers import AssetProblemSerializer

        serializer = AssetProblemSerializer(problem, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], permission_classes=[IsAuthenticatedOrReadOnly])
    def get_problems(self, request, pk=None):
        """Get list of problems for an asset."""
        asset = self.get_object()
        from .models import AssetProblem
        from .serializers import AssetProblemSerializer

        problems = (
            AssetProblem.objects.filter(asset=asset)
            .prefetch_related("photos")
            .order_by("-created_at")
        )
        serializer = AssetProblemSerializer(problems, many=True, context={"request": request})
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

        serializer = AssetProblemSerializer(problem, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["post"],
        url_path="log-hours",
        permission_classes=[IsStaffOrSigAdmin],
    )
    def log_hours(self, request, pk=None):
        """Atomically add operating hours to ``Asset.hours_used``.

        Body: ``{"hours": <positive int>}``. Used to feed analytics
        utilization metrics and the maintenance forecast (gh #analytics).
        Restricted to staff / SIG admins.
        """
        try:
            increment = int(request.data.get("hours"))
        except (TypeError, ValueError):
            return Response(
                {"detail": "hours must be an integer"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if increment <= 0:
            return Response(
                {"detail": "hours must be positive"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        asset = self.get_object()
        Asset.objects.filter(pk=asset.pk).update(hours_used=F("hours_used") + increment)
        asset.refresh_from_db(fields=["hours_used"])
        return Response(
            {"asset_id": str(asset.id), "hours_used": asset.hours_used},
            status=status.HTTP_200_OK,
        )

    @action(
        detail=True,
        methods=["get"],
        url_path="maintenance-history",
        permission_classes=[IsAuthenticated],
    )
    def maintenance_history(self, request, pk=None):
        """Unified maintenance history for an asset.

        Returns a date-sorted list of both backdated MaintenanceRecord rows
        and closed ThirdPartyWorkOrder rows that touched this asset (direct
        ``asset`` FK or via the multi-asset ``assets`` M2M).

        Query params:
        - ``since`` (YYYY-MM-DD, inclusive) — lower-bound completion date
        - ``until`` (YYYY-MM-DD, inclusive) — upper-bound completion date
        - ``source`` — ``all`` (default), ``historical``, or ``workorder``
        """
        asset = self.get_object()

        def _parse(name):
            raw = request.query_params.get(name)
            if not raw:
                return None
            try:
                return date.fromisoformat(raw)
            except ValueError:
                raise DjangoValidationError(f"{name} must be a YYYY-MM-DD date")

        try:
            since = _parse("since")
            until = _parse("until")
        except DjangoValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        source = request.query_params.get("source", "all")
        if source not in {"all", "historical", "workorder"}:
            return Response(
                {"detail": "source must be one of 'all', 'historical', 'workorder'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        rows = []

        if source in {"all", "historical"}:
            historical_qs = MaintenanceRecord.objects.select_related(
                "vendor", "performed_by_internal"
            ).filter(asset=asset)
            if since:
                historical_qs = historical_qs.filter(completed_on__gte=since)
            if until:
                historical_qs = historical_qs.filter(completed_on__lte=until)
            for rec in historical_qs:
                rows.append(_maintenance_history_row_for_record(rec, request))

        if source in {"all", "workorder"}:
            from maintenance_orders.models import ThirdPartyWorkOrder

            tpwo_qs = (
                ThirdPartyWorkOrder.objects.select_related("vendor")
                .filter(status=ThirdPartyWorkOrder.STATUS_CLOSED)
                .filter(Q(asset=asset) | Q(assets=asset))
                .distinct()
            )
            for wo in tpwo_qs:
                row = _maintenance_history_row_for_tpwo(wo)
                if row is None:
                    continue
                if since and row["completed_on"] < since:
                    continue
                if until and row["completed_on"] > until:
                    continue
                rows.append(row)

        rows.sort(
            key=lambda r: (r["completed_on"], r.get("_recorded_at") or ""),
            reverse=True,
        )
        for row in rows:
            row.pop("_recorded_at", None)
            row["completed_on"] = row["completed_on"].isoformat()

        total_cost = sum(
            (Decimal(r["cost"]) for r in rows if r.get("cost") is not None),
            Decimal("0"),
        )
        return Response(
            {
                "count": len(rows),
                "total_cost": str(total_cost),
                "results": rows,
            }
        )


def _maintenance_history_row_for_record(rec, request):
    """Serialize a MaintenanceRecord into the unified history envelope."""
    attachment_url = None
    if rec.attachment:
        attachment_url = rec.attachment.url
        if request is not None:
            attachment_url = request.build_absolute_uri(attachment_url)
    return {
        "id": str(rec.id),
        "source": "historical",
        "title": rec.title,
        "description": rec.description,
        "completed_on": rec.completed_on,
        "_recorded_at": rec.recorded_at.isoformat() if rec.recorded_at else "",
        "performed_by": {
            "vendor": (
                {"id": str(rec.vendor.id), "name": rec.vendor.name} if rec.vendor_id else None
            ),
            "internal_user": (
                {
                    "id": rec.performed_by_internal.id,
                    "username": rec.performed_by_internal.username,
                }
                if rec.performed_by_internal_id
                else None
            ),
        },
        "cost": str(rec.cost) if rec.cost is not None else None,
        "invoice_number": rec.invoice_number,
        "notes": rec.notes,
        "attachment_url": attachment_url,
        "detail_url": None,
    }


def _maintenance_history_row_for_tpwo(wo):
    """Serialize a closed ThirdPartyWorkOrder into the unified history envelope."""
    if wo.closed_at:
        completed = wo.closed_at.date()
    elif wo.downtime_end:
        completed = wo.downtime_end.date()
    else:
        return None
    return {
        "id": str(wo.id),
        "source": "workorder",
        "title": wo.title,
        "description": wo.notes or "",
        "completed_on": completed,
        "_recorded_at": wo.closed_at.isoformat() if wo.closed_at else "",
        "performed_by": {
            "vendor": ({"id": str(wo.vendor.id), "name": wo.vendor.name} if wo.vendor_id else None),
            "internal_user": None,
        },
        "cost": (str(wo.actual_invoice_total) if wo.actual_invoice_total is not None else None),
        "invoice_number": "",
        "notes": wo.notes or "",
        "attachment_url": None,
        "detail_url": f"/work-orders/{wo.id}",
    }


class AssetProblemViewSet(viewsets.ReadOnlyModelViewSet):
    """API endpoint for asset problem reports.

    Read-only here; problems are created via Asset.report_problem. This viewset
    exposes detail GET plus the upload-photo action so reporters can attach
    images to a freshly-created problem report.
    """

    queryset = AssetProblem.objects.select_related("asset", "part").prefetch_related("photos")
    serializer_class = AssetProblemSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        """Honor the ``?asset=``, ``?status=``, and ``?part=`` query filters.

        Clients (ScanTTY asset detail) GET ``?asset={id}`` to see only that
        asset's problems; without this override the list returns *every*
        problem for every asset. Filtering is done here in ``get_queryset``
        rather than via ``filterset_fields`` because django-filter is not a
        dependency of this project — this mirrors the sibling
        ``LocationProblemViewSet``. Unfiltered requests still return all
        problems (dashboard use).
        """
        qs = super().get_queryset()
        asset_id = self.request.query_params.get("asset")
        if asset_id:
            qs = qs.filter(asset_id=asset_id)
        status_filter = self.request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        part_id = self.request.query_params.get("part")
        if part_id:
            qs = qs.filter(part_id=part_id)
        return qs

    @action(
        detail=True,
        methods=["post"],
        url_path="upload-photo",
        permission_classes=[AllowAny],
        parser_classes=[MultiPartParser, FormParser],
    )
    def upload_photo(self, request, pk=None):
        """Attach a photo to this problem report.

        Authorization: the reporter (matched by username) or staff. Anonymous
        reporters created the problem with reported_by="" — those are open
        until staff review, so we accept anonymous photo uploads against them.
        """
        problem = self.get_object()
        user = request.user

        is_staff = bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))
        is_reporter = bool(
            user
            and user.is_authenticated
            and problem.reported_by
            and user.username == problem.reported_by
        )
        is_anonymous_report = not problem.reported_by

        if not (is_staff or is_reporter or is_anonymous_report):
            return Response(
                {"detail": "Not authorized to attach photos to this problem."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = AssetProblemPhotoSerializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        uploaded_by = user if (user and user.is_authenticated) else None
        serializer.save(problem=problem, uploaded_by=uploaded_by)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class AssetDocumentViewSet(viewsets.ModelViewSet):
    """API endpoint for an asset's document library (EAM P1.3).

    Manuals, CAD sources, wiring diagrams, cut-sheets, and cut-ready templates
    (DXF/SVG/G-code/STL) that live WITH a machine. Read is open (mirrors
    ``AssetViewSet``); create/update/delete require authentication.

    Lightweight versioning: POST a document with ``supersedes=<prior id>`` (or
    use the ``supersede`` detail action) to upload a new version — the server
    bumps ``version`` and flips the prior document's ``is_current`` to False so
    it drops out of the current view.
    """

    queryset = AssetDocument.objects.select_related("asset", "uploaded_by", "supersedes").all()
    serializer_class = AssetDocumentSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_queryset(self):
        """Honor ``?asset=``, ``?category=``, and ``?is_current=`` filters.

        The asset detail page GETs ``?asset={id}`` for one asset's library, and
        ``?is_current=true`` for the "current" view (superseded versions live
        behind a toggle). Filtering is done here rather than via
        ``filterset_fields`` because django-filter is not a dependency of this
        project — this mirrors ``AssetProblemViewSet``/``LocationProblemViewSet``.
        """
        qs = super().get_queryset()
        asset_id = self.request.query_params.get("asset")
        if asset_id:
            qs = qs.filter(asset_id=asset_id)
        category = self.request.query_params.get("category")
        if category:
            qs = qs.filter(category=category)
        is_current = self.request.query_params.get("is_current")
        if is_current is not None:
            qs = qs.filter(is_current=is_current.lower() in ("1", "true", "yes"))
        return qs

    def perform_create(self, serializer):
        """Stamp the uploader and apply versioning when ``supersedes`` is set.

        A brand-new document is version 1. A document that supersedes an
        existing one takes ``prior.version + 1`` and flips the prior document's
        ``is_current`` to False so people don't follow a stale manual.
        """
        user = self.request.user if self.request.user.is_authenticated else None
        supersedes = serializer.validated_data.get("supersedes")
        version = (supersedes.version + 1) if supersedes is not None else 1
        serializer.save(uploaded_by=user, version=version)
        if supersedes is not None:
            AssetDocument.objects.filter(pk=supersedes.pk).update(is_current=False)

    @action(
        detail=True,
        methods=["post"],
        parser_classes=[MultiPartParser, FormParser],
    )
    def supersede(self, request, pk=None):
        """Upload a new version that replaces this document.

        The new document inherits this document's ``asset`` (and its
        ``category``/``title`` unless the caller overrides them), links
        ``supersedes`` back to it, bumps ``version``, and flips this document's
        ``is_current`` to False. Requires authentication (write action).
        """
        prior = self.get_object()
        data = request.data.copy()
        data["supersedes"] = str(prior.pk)
        data.setdefault("asset", str(prior.asset_id))
        data.setdefault("category", prior.category)
        data.setdefault("title", prior.title)
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)


def _parse_bool(value, *, default=False):
    """Coerce a request-body value to bool, accepting bools and string forms."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _parse_datetime(value):
    """Parse an ISO-8601 string to an aware datetime, or None if blank/unparseable."""
    if not value or not isinstance(value, str):
        return None
    from django.utils.dateparse import parse_datetime

    dt = parse_datetime(value)
    if dt is not None and timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return dt


class AssetMeterViewSet(viewsets.ModelViewSet):
    """API endpoint for asset usage meters (EAM bead-1).

    A meter is a named cumulative counter on an asset (runtime hours, gallons,
    cycles, kWh, …). This viewset is CRUD over meter *definitions* plus the two
    manual-entry actions that make the manual-first flow usable:

    * ``record-reading`` — enter a measured/estimated reading, absolute (the
      counter now reads N) or delta (add N). This is how a human enters the
      fountain's gallon counter.
    * ``adjust`` — post a correction to a target value with a required reason
      (source ``manual_adjust``).

    Everything here — CRUD and both actions — requires staff / SIG admin, the
    same gate as the log-hours endpoint. Regular users still SEE meters via the
    nested ``meters`` on the asset detail payload and the read-only reading
    endpoint. Filter by ``?asset=<id>``; filterless list returns all meters.
    Manual entry and auto rollup share one write path
    (:func:`inventory.services.meter_sources.apply_reading`) so ``current_value``,
    the ledger, and the hours_used dual-write always stay consistent.
    """

    queryset = AssetMeter.objects.select_related("asset").all()
    serializer_class = AssetMeterSerializer
    permission_classes = [IsStaffOrSigAdmin]

    def get_queryset(self):
        """Honor ``?asset=`` and ``?is_active=`` filters.

        Manual query-param filtering (not filterset_fields) because django-filter
        is not a dependency of this project — mirrors AssetDocumentViewSet /
        LocationProblemViewSet.
        """
        qs = super().get_queryset()
        asset_id = self.request.query_params.get("asset")
        if asset_id:
            qs = qs.filter(asset_id=asset_id)
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() in ("1", "true", "yes"))
        return qs

    def _apply_and_respond(self, meter, spec):
        """Apply a reading via the shared service and return meter + reading."""
        from .services.meter_sources import apply_reading

        user = self.request.user if self.request.user.is_authenticated else None
        reading = apply_reading(meter, spec, recorded_by=user)
        meter.refresh_from_db()
        return Response(
            {
                "meter": AssetMeterSerializer(meter, context={"request": self.request}).data,
                "reading": AssetMeterReadingSerializer(
                    reading, context={"request": self.request}
                ).data,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], url_path="record-reading")
    def record_reading(self, request, pk=None):
        """Record a manual meter reading (source ``manual``).

        Body: ``{"value": <number>, "is_absolute": <bool, default true>,
        "is_estimated": <bool, default false>, "observed_at": <iso8601, optional>}``.
        An absolute reading sets the meter to ``value``; a delta reading adds
        ``value``. ``is_estimated`` marks a human eyeball vs a measured read.
        """
        from .services.meter_sources import ReadingSpec

        meter = self.get_object()
        try:
            value = Decimal(str(request.data.get("value")))
        except (TypeError, ValueError, InvalidOperation):
            return Response(
                {"detail": "value must be a number"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        is_absolute = _parse_bool(request.data.get("is_absolute"), default=True)
        is_estimated = _parse_bool(request.data.get("is_estimated"), default=False)
        observed_at = _parse_datetime(request.data.get("observed_at")) or timezone.now()

        spec_kwargs = {"absolute": value} if is_absolute else {"delta": value}
        spec = ReadingSpec(
            source=AssetMeterReading.SOURCE_MANUAL,
            observed_at=observed_at,
            is_estimated=is_estimated,
            source_ref="manual entry",
            **spec_kwargs,
        )
        return self._apply_and_respond(meter, spec)

    @action(detail=True, methods=["post"])
    def adjust(self, request, pk=None):
        """Post a correction to a target value (source ``manual_adjust``).

        Body: ``{"target": <number>, "reason": <str, required>}``. Writes a
        signed ledger row that moves the meter to ``target`` and records the
        reason in ``notes`` so the correction is auditable.
        """
        from .services.meter_sources import ReadingSpec

        meter = self.get_object()
        try:
            target = Decimal(str(request.data.get("target")))
        except (TypeError, ValueError, InvalidOperation):
            return Response(
                {"detail": "target must be a number"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        reason = str(request.data.get("reason") or "").strip()
        if not reason:
            return Response(
                {"detail": "reason is required for an adjustment"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        spec = ReadingSpec(
            source=AssetMeterReading.SOURCE_MANUAL_ADJUST,
            observed_at=timezone.now(),
            absolute=target,
            is_estimated=False,
            source_ref="manual adjustment",
            notes=reason,
        )
        return self._apply_and_respond(meter, spec)


class AssetMeterReadingViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only API for the append-only meter reading ledger (EAM bead-1).

    Readings are written only via the rollup and the record-reading / adjust
    actions, never through this endpoint. Any authenticated user can read the
    history; filter by ``?meter=<id>`` for one meter's ledger.
    """

    queryset = AssetMeterReading.objects.select_related("meter", "recorded_by").all()
    serializer_class = AssetMeterReadingSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        meter_id = self.request.query_params.get("meter")
        if meter_id:
            qs = qs.filter(meter_id=meter_id)
        asset_id = self.request.query_params.get("asset")
        if asset_id:
            qs = qs.filter(meter__asset_id=asset_id)
        return qs


class LocationProblemViewSet(viewsets.ReadOnlyModelViewSet):
    """API endpoint for location problem reports.

    Read-only here; reports are created via Location.report_problem. This
    viewset exposes detail GET, the promote-to-WO actions, and the
    resolve action so staff can close out a report.
    """

    queryset = LocationProblem.objects.select_related(
        "location", "work_order", "third_party_work_order"
    )
    serializer_class = LocationProblemSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        qs = super().get_queryset()
        location_id = self.request.query_params.get("location")
        if location_id:
            qs = qs.filter(location_id=location_id)
        status_filter = self.request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        severity = self.request.query_params.get("severity")
        if severity:
            qs = qs.filter(severity=severity)
        return qs

    @action(
        detail=True,
        methods=["post"],
        url_path="promote-standard",
        permission_classes=[IsAuthenticated],
    )
    def promote_to_standard_work_order(self, request, pk=None):
        """Promote this LocationProblem to a standard PM WorkOrder.

        Required: ``maintenance_item`` (uuid). Existing WorkOrder model is
        bound to a MaintenanceItem (which is asset-rooted), so the caller
        picks the MaintenanceItem under which to track the work — typically
        a building-level "as-needed" item attached to an Asset that
        represents the location.
        """
        problem = self.get_object()
        if problem.work_order_id:
            return Response(
                {"error": "Already promoted to a standard work order."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        mi_id = request.data.get("maintenance_item")
        if not mi_id:
            return Response(
                {"error": "maintenance_item is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            mi = MaintenanceItem.objects.get(id=mi_id)
        except MaintenanceItem.DoesNotExist:
            return Response(
                {"error": "MaintenanceItem not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        with transaction.atomic():
            wo = WorkOrder.objects.create(
                maintenance_item=mi,
                notes=problem.description,
                assigned_to=request.user if request.user.is_authenticated else None,
            )
            problem.work_order = wo
            problem.status = LocationProblem.IN_PROGRESS
            problem.save(update_fields=["work_order", "status", "updated_at"])
            self._copy_attachments_to_work_order(problem, wo)

        serializer = LocationProblemSerializer(problem, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=["post"],
        url_path="promote-third-party",
        permission_classes=[IsAuthenticated],
    )
    def promote_to_third_party_work_order(self, request, pk=None):
        """Promote this LocationProblem to a ThirdPartyWorkOrder.

        Required: ``vendor`` (uuid) and ``title``. Pre-fills the WO with
        the problem description (in ``notes``), links the WO back to the
        problem's location, and copies any attached photo or paper-form
        PDF to the new WO as attachments.
        """
        from maintenance_orders.models import ThirdPartyWorkOrder, ThirdPartyWorkOrderAttachment
        from vendors.models import Vendor

        problem = self.get_object()
        if problem.third_party_work_order_id:
            return Response(
                {"error": "Already promoted to a third-party work order."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        vendor_id = request.data.get("vendor")
        title = (request.data.get("title") or "").strip()
        if not vendor_id or not title:
            return Response(
                {"error": "vendor and title are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            vendor = Vendor.objects.get(id=vendor_id)
        except Vendor.DoesNotExist:
            return Response(
                {"error": "Vendor not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        work_type = request.data.get("work_type") or ThirdPartyWorkOrder.WORK_TYPE_STANDARD

        with transaction.atomic():
            tpwo = ThirdPartyWorkOrder.objects.create(
                title=title,
                location=problem.location,
                vendor=vendor,
                work_type=work_type,
                notes=problem.description,
                opened_by=request.user if request.user.is_authenticated else None,
            )
            if problem.photo:
                self._copy_to_tpwo_attachment(
                    problem.photo,
                    tpwo,
                    kind=ThirdPartyWorkOrderAttachment.KIND_PHOTO,
                    caption=f"Reporter photo from LocationProblem {problem.id}",
                    filename_hint="location-problem-photo",
                    user=request.user if request.user.is_authenticated else None,
                )
            if problem.paper_form_attachment:
                self._copy_to_tpwo_attachment(
                    problem.paper_form_attachment,
                    tpwo,
                    kind=ThirdPartyWorkOrderAttachment.KIND_PAPER_FORM,
                    caption=f"Original paper form from LocationProblem {problem.id}",
                    filename_hint="location-problem-paper-form",
                    user=request.user if request.user.is_authenticated else None,
                )
            problem.third_party_work_order = tpwo
            problem.status = LocationProblem.IN_PROGRESS
            problem.save(update_fields=["third_party_work_order", "status", "updated_at"])

        serializer = LocationProblemSerializer(problem, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def resolve(self, request, pk=None):
        """Mark this location problem as resolved or closed."""
        problem = self.get_object()
        new_status = request.data.get("status", LocationProblem.RESOLVED)
        if new_status not in (LocationProblem.RESOLVED, LocationProblem.CLOSED):
            return Response(
                {
                    "error": (
                        f"status must be '{LocationProblem.RESOLVED}' or "
                        f"'{LocationProblem.CLOSED}'"
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        problem.status = new_status
        problem.resolution_notes = request.data.get(
            "resolution_notes", problem.resolution_notes or ""
        )
        if not problem.resolved_at:
            problem.resolved_at = timezone.now()
            if request.user.is_authenticated:
                problem.resolved_by = getattr(request.user, "handle", None) or request.user.username
        problem.save()

        record_maintenance_audit_event(
            action="location_problem_resolve",
            actor=request.user,
            location_problem=problem,
            notes=problem.resolution_notes or "",
            metadata={
                "new_status": new_status,
                "severity": problem.severity,
            },
        )

        serializer = LocationProblemSerializer(problem, context={"request": request})
        return Response(serializer.data)

    @staticmethod
    def _copy_attachments_to_work_order(problem, work_order):
        """Copy a problem's photo to a PM WorkOrder via WorkOrderPhoto."""
        from .models import WorkOrderPhoto

        if problem.photo:
            wop = WorkOrderPhoto(
                work_order=work_order,
                caption=f"From LocationProblem {problem.id}",
            )
            problem.photo.open("rb")
            try:
                from django.core.files.base import ContentFile

                wop.image.save(
                    f"location-problem-{problem.id}.jpg",
                    ContentFile(problem.photo.read()),
                    save=False,
                )
            finally:
                problem.photo.close()
            wop.save()

    @staticmethod
    def _copy_to_tpwo_attachment(file_field, tpwo, *, kind, caption, filename_hint, user):
        from django.core.files.base import ContentFile

        from maintenance_orders.models import ThirdPartyWorkOrderAttachment

        file_field.open("rb")
        try:
            data = file_field.read()
        finally:
            file_field.close()
        attachment = ThirdPartyWorkOrderAttachment(
            work_order=tpwo,
            kind=kind,
            caption=caption,
            uploaded_by=user,
        )
        ext = file_field.name.rsplit(".", 1)[-1] if "." in file_field.name else "bin"
        attachment.file.save(
            f"{filename_hint}-{tpwo.short_id}.{ext}",
            ContentFile(data),
            save=False,
        )
        attachment.save()


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
        """Mark a part as replaced (updates last_replaced_at to now).

        For serialized parts, an optional ``replacement_serial_number`` in the
        request body records the serial of the newly installed unit. Omitting
        it (or sending an empty value) preserves the original one-click
        behaviour used by non-serialized parts.
        """
        from django.utils import timezone

        asset_part = self.get_object()
        update_fields = ["last_replaced_at"]

        raw_serial = request.data.get("replacement_serial_number")
        if raw_serial not in (None, ""):
            if not isinstance(raw_serial, str):
                raise serializers.ValidationError(
                    {"replacement_serial_number": "Must be a string."}
                )
            serial = raw_serial.strip()
            if serial:
                max_length = AssetPart._meta.get_field("replacement_serial_number").max_length
                if len(serial) > max_length:
                    raise serializers.ValidationError(
                        {
                            "replacement_serial_number": (
                                f"Ensure this value has at most {max_length} " "characters."
                            )
                        }
                    )
                asset_part.replacement_serial_number = serial
                update_fields.append("replacement_serial_number")

        asset_part.last_replaced_at = timezone.now()
        asset_part.save(update_fields=update_fields)

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


class InventoryReportViewSet(viewsets.ViewSet):
    """API endpoint for inventory reports."""

    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["get"])
    def stock_by_category(self, request):
        """Get stock levels aggregated by category."""
        from django.db.models import Avg, Count, OuterRef, Q, Subquery, Sum, Value
        from django.db.models.functions import Coalesce

        from inventory.models import ItemSupplier

        # Subquery: average unit_cost across all active suppliers per item
        avg_cost_subquery = (
            ItemSupplier.objects.filter(
                item=OuterRef("pk"), is_active=True, unit_cost__isnull=False
            )
            .values("item")
            .annotate(avg=Avg("unit_cost"))
            .values("avg")
        )

        queryset = (
            InventoryItem.objects.filter(is_active=True)
            .select_related("category")
            .annotate(
                category_id_coalesced=Coalesce("category__id", Value(0)),
                category_name_coalesced=Coalesce("category__name", Value("Uncategorized")),
                unit_cost_value=Subquery(avg_cost_subquery),
            )
            .values("category_id_coalesced", "category_name_coalesced")
            .annotate(
                total_items=Count("id"),
                total_stock=Sum("current_stock"),
                total_value=Sum(
                    F("current_stock") * Coalesce("unit_cost_value", Value(0)),
                    output_field=models.DecimalField(max_digits=20, decimal_places=2),
                ),
                low_stock_count=Count(
                    "id",
                    filter=Q(current_stock__lte=F("minimum_stock")) & Q(is_retired=False),
                ),
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
        from datetime import datetime, timedelta

        from django.db.models import Count

        from reorder_queue.models import ReorderRequest

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

        # Get reorder requests in the time period
        reorder_requests = (
            ReorderRequest.objects.filter(
                requested_at__date__gte=start_date,
                requested_at__date__lte=end_date,
            )
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
        from django.db.models import Avg, Count, OuterRef, Subquery, Sum, Value
        from django.db.models.functions import Coalesce

        from inventory.models import ItemSupplier

        # Subquery: average unit_cost across all active suppliers per item
        avg_cost_subquery = (
            ItemSupplier.objects.filter(
                item=OuterRef("pk"), is_active=True, unit_cost__isnull=False
            )
            .values("item")
            .annotate(avg=Avg("unit_cost"))
            .values("avg")
        )

        queryset = (
            InventoryItem.objects.filter(is_active=True)
            .select_related("location")
            .annotate(
                location_id_coalesced=Coalesce("location__id", Value(0)),
                location_name_coalesced=Coalesce("location__name", Value("No Location")),
                unit_cost_value=Subquery(avg_cost_subquery),
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
    def serialized_forecast(self, request):
        """Consumption forecast + low-stock report for serialized components.

        Mode-aware: consumable units deplete on ``consume`` while reusable units
        deplete only on ``retire``/``dispose`` (the install/remove reuse cycle
        does not reduce stock). Returns one row per active serialized item with
        ``avg_daily_use``, ``days_until_stockout`` and ``reorder_point`` so the
        inventory + purchasing overview dashboards can surface what is running
        low and what to reorder.

        Query params:
            ``window_days`` — trailing window for the depletion rate (default
            90). ``low_stock_only`` — when truthy, only items at/below their
            reorder point are returned.
        """
        from inventory.services.component_forecast import (
            DEFAULT_WINDOW_DAYS,
            build_component_forecast,
        )

        try:
            window_days = int(request.query_params.get("window_days", DEFAULT_WINDOW_DAYS))
        except (TypeError, ValueError):
            window_days = DEFAULT_WINDOW_DAYS

        low_stock_only = str(request.query_params.get("low_stock_only", "")).lower() in (
            "1",
            "true",
            "yes",
        )

        data = build_component_forecast(
            window_days=window_days,
            low_stock_only=low_stock_only,
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

        return error_response(ErrorCode.VALIDATION_FAILED, "Invalid report type")


class MaintenanceMaterialViewSet(viewsets.ModelViewSet):
    """API endpoint for maintenance materials."""

    queryset = MaintenanceMaterial.objects.select_related("maintenance_item__asset").all()
    serializer_class = MaintenanceMaterialSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        queryset = super().get_queryset()
        maintenance_item = self.request.query_params.get("maintenance_item")
        if maintenance_item:
            queryset = queryset.filter(maintenance_item_id=maintenance_item)
        return queryset


class MaintenanceLogViewSet(viewsets.ReadOnlyModelViewSet):
    """API endpoint for maintenance completion logs (read-only list/detail)."""

    queryset = MaintenanceLog.objects.select_related(
        "maintenance_item__asset", "completed_by"
    ).all()
    serializer_class = MaintenanceLogSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        maintenance_item = self.request.query_params.get("maintenance_item")
        if maintenance_item:
            queryset = queryset.filter(maintenance_item_id=maintenance_item)
        asset = self.request.query_params.get("asset")
        if asset:
            queryset = queryset.filter(maintenance_item__asset_id=asset)
        return queryset


class MaintenanceTaskViewSet(viewsets.ModelViewSet):
    """API endpoint for maintenance task steps (line items within a MaintenanceItem)."""

    queryset = MaintenanceTask.objects.select_related("maintenance_item__asset").all()
    serializer_class = MaintenanceTaskSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        maintenance_item = self.request.query_params.get("maintenance_item")
        if maintenance_item:
            queryset = queryset.filter(maintenance_item_id=maintenance_item)
        return queryset


class WorkOrderViewSet(viewsets.ModelViewSet):
    """API endpoint for preventive maintenance work orders."""

    queryset = (
        WorkOrder.objects.select_related(
            "maintenance_item__asset__location",
            "maintenance_item__asset__manufacturer",
            "maintenance_item__asset__category",
            "assigned_to",
        )
        .prefetch_related(
            "task_completions__completed_by",
            "task_completions__task",
            "material_usage__material",
            "photos__uploaded_by",
            "maintenance_item__materials",
        )
        .all()
    )
    # gh #374: read for any authenticated user (volunteers can see open +
    # completed PM work orders), but write requires staff / Logistics / SIG
    # admin. Third-party work orders use a stricter gate (IsStaffOrSigAdmin)
    # because the operator-set rule hides them from volunteers entirely.
    permission_classes = [IsAuthenticatedOrStaffSigAdminWrite]

    def get_serializer_class(self):
        if self.action == "list":
            return WorkOrderListSerializer
        return WorkOrderSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        maintenance_item = self.request.query_params.get("maintenance_item")
        if maintenance_item:
            queryset = queryset.filter(maintenance_item_id=maintenance_item)
        asset = self.request.query_params.get("asset")
        if asset:
            queryset = queryset.filter(maintenance_item__asset_id=asset)
        wo_status = self.request.query_params.get("status")
        if wo_status:
            queryset = queryset.filter(status=wo_status)
        return queryset

    @staticmethod
    def _has_complete_validation(work_order) -> bool:
        """AC-3: True iff the WO has at least one fully-acknowledged validation."""
        return work_order.validations.filter(
            electrical_acknowledged=True,
            loto_acknowledged=True,
            required_fields_acknowledged=True,
        ).exists()

    def _check_completion_gate(self, request):
        """AC-3: 412 if a status=completed transition lacks validation."""
        instance = self.get_object()
        new_status = request.data.get("status")
        if (
            new_status == WorkOrder.STATUS_COMPLETED
            and instance.status != WorkOrder.STATUS_COMPLETED
            and not self._has_complete_validation(instance)
        ):
            return Response(
                {
                    "detail": (
                        "This work order requires a validation acknowledgement "
                        "(electrical, LOTO, required fields) before it can be "
                        "marked completed."
                    ),
                    "code": "validation_required",
                },
                status=status.HTTP_412_PRECONDITION_FAILED,
            )
        return None

    @staticmethod
    def _sync_completion_timestamp(work_order, *, was_completed: bool) -> None:
        """Keep completed_at in step with the completed status.

        completed_at is read-only over the API, so the server owns it: stamp it
        when a work order becomes completed (the digital-completion path used to
        leave it null, surfacing as "Completed N/A" in the asset view) and clear
        it if a completed work order is reopened. An already-set timestamp (e.g.
        from the paper-form ingest) is left untouched.
        """
        if work_order.status == WorkOrder.STATUS_COMPLETED:
            if work_order.completed_at is None:
                work_order.completed_at = timezone.now()
                work_order.save(update_fields=["completed_at"])
        elif was_completed and work_order.completed_at is not None:
            work_order.completed_at = None
            work_order.save(update_fields=["completed_at"])

    @staticmethod
    def _sync_maintenance_item_completion(work_order, *, actor=None) -> None:
        """Bubble a WO completion up to its MaintenanceItem.

        When a WO that's linked to a MaintenanceItem becomes ``completed``,
        we write a MaintenanceLog and bump ``MaintenanceItem.last_completed_at``
        — same side effects the manual "Log maintenance" button has via
        ``MaintenanceItemViewSet.complete``. Without this, a maker who
        only ever closes work orders sees the PM keep nagging because the
        item's last_completed_at stays null. (Issue surfaced 2026-06-12
        on the Water Fountain "Replace Water Filter" PM: two completed
        WOs against the item, zero MaintenanceLog rows, is_overdue still
        true.)

        Dedupe: the new MaintenanceLog.work_order FK lets us no-op when
        a log for this WO already exists, so a WO that's bounced
        completed → reopened → completed doesn't double-log.

        ``last_completed_at`` only advances forward — a reopen + recomplete
        with an earlier ``wo.completed_at`` doesn't roll the date back.
        """
        if work_order.status != WorkOrder.STATUS_COMPLETED:
            return
        if not work_order.completed_at:
            return

        actor_to_record = actor if actor and getattr(actor, "is_authenticated", False) else None

        # Gather every item that should get the side effect: the
        # primary FK plus any bundled siblings (auto-bundle window
        # set on perform_create). pk being None means we haven't
        # saved yet — skip the M2M lookup in that case.
        item_ids: list = []
        if work_order.maintenance_item_id is not None:
            item_ids.append(work_order.maintenance_item_id)
        if work_order.pk is not None:
            for bundled_id in work_order.additional_maintenance_items.values_list("id", flat=True):
                if bundled_id not in item_ids:
                    item_ids.append(bundled_id)

        for item_id in item_ids:
            existing = MaintenanceLog.objects.filter(
                work_order=work_order, maintenance_item_id=item_id
            ).first()
            if existing is None:
                log = MaintenanceLog.objects.create(
                    maintenance_item_id=item_id,
                    work_order=work_order,
                    completed_by=actor_to_record,
                    notes=(
                        f"Auto-logged from work order {work_order.short_id}. "
                        f"WO status: completed."
                    ),
                )
                # completed_at is auto_now_add; override to match the
                # WO's completion timestamp via an explicit UPDATE so
                # the log reflects when the work was actually done.
                MaintenanceLog.objects.filter(pk=log.pk).update(
                    completed_at=work_order.completed_at
                )

            item = MaintenanceItem.objects.filter(pk=item_id).first()
            if item is None:
                continue
            if item.last_completed_at is None or work_order.completed_at > item.last_completed_at:
                item.last_completed_at = work_order.completed_at
                item.save(update_fields=["last_completed_at"])

    @staticmethod
    def _bundle_due_siblings(work_order) -> None:
        """Roll same-asset PMs due within
        ``SiteSettings.pm_auto_bundle_due_within_days`` into this WO.

        Runs on perform_create only. Skips when:
          - the SiteSettings value is 0 (auto-bundle disabled — default)
          - the WO has no primary maintenance_item
          - the primary item has no asset

        Siblings are MaintenanceItems on the same asset, is_active=True,
        NOT the primary item, whose next_due_at is within window_days
        of now OR are already overdue. For each sibling we materialize
        its tasks as WorkOrderTaskCompletion rows so the existing
        per-step UI renders one combined checklist.

        The window is a live ``SiteSettings`` value (admin-editable from
        the site-settings admin page) rather than env config, so an
        admin can flip the threshold without a redeploy.
        """
        from customization.models import SiteSettings

        try:
            window_days = int(SiteSettings.get().pm_auto_bundle_due_within_days or 0)
        except Exception:  # noqa: BLE001 — broken row should not crash WO create
            window_days = 0
        if window_days <= 0:
            return
        primary = work_order.maintenance_item
        if primary is None or primary.asset_id is None:
            return

        cutoff = timezone.now() + timedelta(days=window_days)
        siblings_qs = MaintenanceItem.objects.filter(
            asset_id=primary.asset_id, is_active=True
        ).exclude(pk=primary.pk)

        bundled_ids: list = []
        for sib in siblings_qs:
            next_due = sib.next_due_at
            if next_due is None and not sib.is_overdue:
                continue
            if next_due is not None and next_due > cutoff:
                continue
            bundled_ids.append(sib.pk)

        if not bundled_ids:
            return

        work_order.additional_maintenance_items.add(*bundled_ids)

        # Materialize task_completions for the primary item AND every
        # bundled sibling so the per-step UI renders one combined
        # checklist. The DRF create path doesn't otherwise materialize
        # the primary item's tasks (only ``generate_work_order`` does),
        # so we include it here when bundling fires to keep the
        # per-item checkbox UX consistent. Each row denormalizes
        # title / order / required from MaintenanceTask; per-item
        # grouping in the UI uses task.maintenance_item_id.
        from .models import MaintenanceTask, WorkOrderTaskCompletion

        existing_task_ids = set(work_order.task_completions.values_list("task_id", flat=True))
        item_ids_to_materialize = [primary.pk] + bundled_ids
        new_rows = []
        for item_id in item_ids_to_materialize:
            for task in MaintenanceTask.objects.filter(maintenance_item_id=item_id).order_by(
                "order", "title"
            ):
                if task.pk in existing_task_ids:
                    continue
                new_rows.append(
                    WorkOrderTaskCompletion(
                        work_order=work_order,
                        task=task,
                        task_title=task.title,
                        task_order=task.order,
                        is_required=task.is_required,
                    )
                )
        if new_rows:
            WorkOrderTaskCompletion.objects.bulk_create(new_rows)

    def perform_create(self, serializer):
        work_order = serializer.save()
        self._sync_completion_timestamp(work_order, was_completed=False)
        # Roll same-asset PMs due within PM_AUTO_BUNDLE_DUE_WITHIN_DAYS
        # BEFORE the completion cascade, so a new WO that lands in
        # status=completed (rare, but possible via paper-form ingest)
        # also closes every bundled sibling.
        self._bundle_due_siblings(work_order)
        self._sync_maintenance_item_completion(work_order, actor=self.request.user)
        record_maintenance_audit_event(
            action="wo_create",
            actor=self.request.user,
            work_order=work_order,
            metadata={
                "maintenance_item_id": str(work_order.maintenance_item_id),
                "due_date": work_order.due_date.isoformat() if work_order.due_date else None,
            },
        )

    def perform_update(self, serializer):
        old_status = serializer.instance.status
        work_order = serializer.save()
        self._sync_completion_timestamp(
            work_order, was_completed=(old_status == WorkOrder.STATUS_COMPLETED)
        )
        self._sync_maintenance_item_completion(work_order, actor=self.request.user)
        if (
            work_order.status == WorkOrder.STATUS_COMPLETED
            and old_status != WorkOrder.STATUS_COMPLETED
        ):
            record_maintenance_audit_event(
                action="wo_complete",
                actor=self.request.user,
                work_order=work_order,
                metadata={
                    "previous_status": old_status,
                    "completed_at": (
                        work_order.completed_at.isoformat() if work_order.completed_at else None
                    ),
                },
            )

    def update(self, request, *args, **kwargs):
        gate = self._check_completion_gate(request)
        if gate is not None:
            return gate
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        gate = self._check_completion_gate(request)
        if gate is not None:
            return gate
        return super().partial_update(request, *args, **kwargs)

    def _render_wo_pdf(self, work_order, base_url):
        """Shared work-order PDF renderer for :meth:`pdf` and :meth:`omr_pdf`.

        Unifies both endpoints on the OMR variant (owner directive: one PDF
        generation route, not two). ``build_and_persist_omr_template`` renders a
        PDF that is a strict SUPERSET of the plain form — the same interactive
        AcroForm checkboxes and layout PLUS 4 corner fiducials — and upserts the
        ``WorkOrderOmrTemplate`` region map. So every printed sheet is both
        digitally fillable (on-screen/emailed AcroForm) and scan-to-complete
        capable, from a single generation route.

        AC-3 (oms-2da): gated on at least one fully-acknowledged validation
        record. The frontend shows the validation modal when this returns 412
        and retries after the user submits the checklist.
        """
        from .services.work_order_omr import build_and_persist_omr_template

        if not self._has_complete_validation(work_order):
            return Response(
                {
                    "detail": (
                        "Confirm the validation checklist (electrical, LOTO, "
                        "required fields) before generating a PDF."
                    ),
                    "code": "validation_required",
                },
                status=status.HTTP_412_PRECONDITION_FAILED,
            )
        pdf_bytes, _template = build_and_persist_omr_template(work_order, base_url=base_url)

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        short_id = work_order.short_id.replace(" ", "-")
        response["Content-Disposition"] = f'inline; filename="work-order-{short_id}.pdf"'
        return response

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        """Generate a printable work-order PDF (OMR scan-to-complete variant).

        Produces the unified OMR form: interactive AcroForm fill PLUS 4 corner
        fiducials and a persisted ``WorkOrderOmrTemplate``, so the one sheet
        serves both emailed-digital completion and flatbed-scan completion.

        AC-3 (oms-2da): gated on at least one fully-acknowledged validation
        record. The frontend shows the validation modal when this returns 412
        and retries after the user submits the checklist.
        """
        work_order = self.get_object()
        base_url = request.build_absolute_uri("/").rstrip("/")
        return self._render_wo_pdf(work_order, base_url)

    @action(detail=True, methods=["get"], url_path="omr-pdf")
    def omr_pdf(self, request, pk=None):
        """DEPRECATED alias for :meth:`pdf` — identical OMR PDF + persisted template.

        Both endpoints now unify on the OMR variant (owner directive: a single
        PDF generation route), so this returns the exact same result as
        ``pdf``. Retained only so the ``omr-pdf`` URL keeps resolving — and the
        API permission matrix stays unchanged — during the frontend rollout that
        collapses the two download buttons into one pointed at ``/pdf/``.
        Removable in a later cleanup once no caller hits this path.
        """
        work_order = self.get_object()
        base_url = request.build_absolute_uri("/").rstrip("/")
        return self._render_wo_pdf(work_order, base_url)

    @action(detail=True, methods=["post"], url_path="validate")
    def validate_checklist(self, request, pk=None):
        """AC-3: record a pre-finalization validation acknowledgement.

        Body: ``{electrical_acknowledged, loto_acknowledged,
        required_fields_acknowledged, notes?}``. All three flags must be
        truthy or the resulting record is treated as incomplete (and won't
        unlock finalize / PDF).
        """
        work_order = self.get_object()
        electrical = bool(request.data.get("electrical_acknowledged"))
        loto = bool(request.data.get("loto_acknowledged"))
        required_fields = bool(request.data.get("required_fields_acknowledged"))
        notes = (request.data.get("notes") or "").strip()

        if not (electrical and loto and required_fields):
            return Response(
                {
                    "detail": (
                        "All three acknowledgements (electrical, LOTO, "
                        "required fields) are required."
                    ),
                    "code": "incomplete_acknowledgement",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        record = WorkOrderValidation.objects.create(
            work_order=work_order,
            validated_by=request.user if request.user.is_authenticated else None,
            electrical_acknowledged=electrical,
            loto_acknowledged=loto,
            required_fields_acknowledged=required_fields,
            notes=notes,
        )
        return Response(
            WorkOrderValidationSerializer(record, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def add_photo(self, request, pk=None):
        """Upload a photo to this work order."""
        work_order = self.get_object()
        serializer = WorkOrderPhotoSerializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save(work_order=work_order, uploaded_by=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["patch"], url_path="tasks/(?P<task_id>[^/.]+)/complete")
    def complete_task(self, request, pk=None, task_id=None):
        """Toggle completion of a specific task step within this work order."""
        work_order = self.get_object()
        try:
            tc = work_order.task_completions.get(id=task_id)
        except WorkOrderTaskCompletion.DoesNotExist:
            return Response(
                {"detail": "Task completion record not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        is_completed = request.data.get("is_completed")
        if is_completed is None:
            return Response(
                {"detail": "is_completed is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        tc.is_completed = bool(is_completed)
        if tc.is_completed and not tc.completed_at:
            tc.completed_at = timezone.now()
            tc.completed_by = request.user
        elif not tc.is_completed:
            tc.completed_at = None
            tc.completed_by = None
        if "notes" in request.data:
            tc.notes = request.data["notes"]
        tc.save()

        # Update work order status to in_progress if any task is completed
        if tc.is_completed and work_order.status == WorkOrder.STATUS_OPEN:
            work_order.status = WorkOrder.STATUS_IN_PROGRESS
            work_order.save(update_fields=["status", "updated_at"])

        return Response(WorkOrderTaskCompletionSerializer(tc).data)

    @action(
        detail=False,
        methods=["post"],
        url_path="upload-pdf",
        parser_classes=[MultiPartParser, FormParser],
    )
    def upload_pdf(self, request):
        """Manually upload a completed work-order PDF (staff only).

        Mirrors the postmark inbound webhook for users who scan paper forms
        themselves rather than emailing them in. Reuses the same
        work_order_ingest pipeline.
        """
        from django.core.files.base import ContentFile

        from .services.work_order_ingest import (
            apply_submission,
            detect_submission_kind,
            looks_like_scan,
        )

        user = request.user
        if not (user.is_authenticated and (user.is_staff or user.is_superuser)):
            return Response(
                {"detail": "Staff access required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        pdf_file = request.FILES.get("pdf")
        if not pdf_file:
            return Response(
                {"detail": "pdf field required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        pdf_bytes = pdf_file.read()
        upload_name = pdf_file.name or ""
        kind = detect_submission_kind(pdf_bytes, subject=upload_name)
        is_image = upload_name.lower().endswith((".jpg", ".jpeg", ".png"))
        is_scan = looks_like_scan(pdf_bytes, is_image=is_image)

        submission = WorkOrderSubmission(
            kind=kind,
            source=(
                WorkOrderSubmission.SOURCE_SCAN if is_scan else WorkOrderSubmission.SOURCE_MANUAL
            ),
            submitted_by=user,
            from_email=(user.email or "")[:254],
            subject=(upload_name)[:500],
            status=WorkOrderSubmission.STATUS_RECEIVED,
        )
        submission.attachment.save(
            pdf_file.name or "work-order.pdf",
            ContentFile(pdf_bytes),
            save=False,
        )
        submission.save()

        apply_submission(submission)
        submission.refresh_from_db()

        completed_items: list[dict] = []
        errors: list[str] = []
        if submission.parse_error:
            errors.append(submission.parse_error)
        if submission.work_order_id:
            checked_ids = [
                tc_id
                for tc_id, checked in (submission.parsed_fields or {})
                .get("task_checks", {})
                .items()
                if checked
            ]
            if checked_ids:
                completed_items = list(
                    submission.work_order.task_completions.filter(
                        id__in=checked_ids,
                        is_completed=True,
                    ).values("id", "task_title")
                )

        return Response(
            {
                "submission_id": str(submission.id),
                "kind": submission.kind,
                "status": submission.status,
                "work_order_id": (
                    str(submission.work_order_id) if submission.work_order_id else None
                ),
                "third_party_work_order_id": (
                    str(submission.third_party_work_order_id)
                    if submission.third_party_work_order_id
                    else None
                ),
                "completed_items": completed_items,
                "errors": errors,
            },
            status=status.HTTP_200_OK,
        )

    @action(
        detail=True,
        methods=["post"],
        url_path="submissions/(?P<submission_id>[^/.]+)/apply-pending",
    )
    def apply_pending_changes(self, request, pk=None, submission_id=None):
        """Accept CV-derived pending changes on a submission (all, or per row).

        Back-compat: with no request body, every applicable change is applied
        and the queue is cleared (``signature``/``handwritten`` → WO notes).

        Per-row (OMR, bead-2): an optional ``target_ids`` list applies only the
        named ``checkbox``/``ink`` marks (pre-checking their task/material) and
        leaves the rest queued. An optional ``confirm_complete`` boolean is the
        HUMAN gate that advances the WO to COMPLETED — a scan never closes a WO
        on its own.
        """
        from .services.work_order_ingest import omr_apply_mark, omr_confirm_completion

        work_order = self.get_object()
        try:
            submission = work_order.submissions.get(id=submission_id)
        except WorkOrderSubmission.DoesNotExist:
            return Response(
                {"detail": "Submission not found for this work order."},
                status=status.HTTP_404_NOT_FOUND,
            )

        body = request.data if isinstance(request.data, dict) else {}
        raw_target_ids = body.get("target_ids")
        target_ids = set(raw_target_ids) if isinstance(raw_target_ids, list) else None
        confirm_complete = bool(body.get("confirm_complete"))

        pending = list(submission.pending_changes or [])
        if not pending and not confirm_complete:
            return Response(
                {"detail": "No pending changes to apply.", "submission_status": submission.status},
                status=status.HTTP_200_OK,
            )

        def _selected(change):
            return target_ids is None or change.get("target_id") in target_ids

        notes_to_add: list[str] = []
        remaining: list[dict] = []
        applied_count = 0
        for change in pending:
            if not _selected(change):
                remaining.append(change)
                continue
            kind = change.get("kind")
            value = change.get("value")
            confidence = change.get("confidence", 0.0) or 0.0
            if kind in ("checkbox", "ink"):
                # Accepting a scanned mark = confirm the task/material is done.
                omr_apply_mark(work_order, change.get("target_id") or "", marked=True)
                applied_count += 1
            elif kind == "signature":
                notes_to_add.append(
                    f"Signature accepted by reviewer (original confidence {confidence:.2f})."
                )
                applied_count += 1
            elif kind == "handwritten" and isinstance(value, str) and value:
                notes_to_add.append(
                    f'Handwritten note accepted (confidence {confidence:.2f}): "{value}"'
                )
                applied_count += 1
            # ``error`` / unknown rows are simply cleared, not applied.

        if notes_to_add:
            existing = work_order.notes or ""
            combined = existing + ("\n" if existing else "") + "\n".join(notes_to_add)
            work_order.notes = combined.strip()
            work_order.save(update_fields=["notes", "updated_at"])

        completed = False
        if confirm_complete:
            completed = omr_confirm_completion(work_order, submission, user=request.user)

        submission.pending_changes = remaining
        submission.status = (
            WorkOrderSubmission.STATUS_APPLIED
            if not remaining
            else WorkOrderSubmission.STATUS_PENDING_REVIEW
        )
        submission.save(update_fields=["pending_changes", "status"])

        return Response(
            {
                "detail": f"Applied {applied_count} pending change(s).",
                "submission_status": submission.status,
                "applied_count": applied_count,
                "work_order_completed": completed,
                "work_order_status": work_order.status,
            },
            status=status.HTTP_200_OK,
        )

    @action(
        detail=True,
        methods=["post"],
        url_path="submissions/(?P<submission_id>[^/.]+)/discard-pending",
    )
    def discard_pending_changes(self, request, pk=None, submission_id=None):
        """Reject pending changes on a submission (all, or per row).

        Back-compat: with no body, the whole queue is dropped. Per-row (OMR):
        an optional ``target_ids`` list rejects only those marks — and any mark
        that was auto-pre-checked (``auto_applied``) is UNDONE on the work order
        so a rejected scan read leaves no trace.
        """
        from .services.work_order_ingest import omr_apply_mark

        work_order = self.get_object()
        try:
            submission = work_order.submissions.get(id=submission_id)
        except WorkOrderSubmission.DoesNotExist:
            return Response(
                {"detail": "Submission not found for this work order."},
                status=status.HTTP_404_NOT_FOUND,
            )

        body = request.data if isinstance(request.data, dict) else {}
        raw_target_ids = body.get("target_ids")
        target_ids = set(raw_target_ids) if isinstance(raw_target_ids, list) else None

        remaining: list[dict] = []
        dropped = 0
        for change in submission.pending_changes or []:
            if target_ids is not None and change.get("target_id") not in target_ids:
                remaining.append(change)
                continue
            # Undo an auto-applied pre-check so a rejected read leaves no mark.
            if change.get("auto_applied") and change.get("kind") in ("checkbox", "ink"):
                omr_apply_mark(work_order, change.get("target_id") or "", marked=False)
            dropped += 1

        submission.pending_changes = remaining
        if not remaining and submission.status == WorkOrderSubmission.STATUS_PENDING_REVIEW:
            submission.status = WorkOrderSubmission.STATUS_APPLIED
        submission.save(update_fields=["pending_changes", "status"])

        return Response(
            {
                "detail": f"Discarded {dropped} pending change(s).",
                "submission_status": submission.status,
                "dropped_count": dropped,
            },
            status=status.HTTP_200_OK,
        )

    @action(
        detail=True,
        methods=["get"],
        url_path="submissions/(?P<submission_id>[^/.]+)/mark-crop/(?P<target_id>[^/.]+)",
        permission_classes=[IsAuthenticated],
    )
    def mark_crop(self, request, pk=None, submission_id=None, target_id=None):
        """Serve a small PNG of one warped OMR mark region for the reviewer.

        Re-warps the stored scan into template space on demand and crops the
        ``target_id`` region (bead-2). Authenticated read-only — rendering a
        crop can never mutate state — so the review screen can eyeball the ink.
        """
        from .services.work_order_ingest import _omr_scan_inputs
        from .services.work_order_omr import render_mark_crop

        work_order = self.get_object()
        try:
            submission = work_order.submissions.get(id=submission_id)
        except WorkOrderSubmission.DoesNotExist:
            return Response(
                {"detail": "Submission not found for this work order."},
                status=status.HTTP_404_NOT_FOUND,
            )

        template = work_order.omr_templates.order_by("-created_at").first()
        if template is None or not submission.attachment:
            return Response(
                {"detail": "No scan crop available for this submission."},
                status=status.HTTP_404_NOT_FOUND,
            )

        submission.attachment.open("rb")
        try:
            raw_bytes = submission.attachment.read()
        finally:
            submission.attachment.close()

        _wo_id, _err, image_bytes = _omr_scan_inputs(raw_bytes)
        if image_bytes is None:
            return Response(
                {"detail": "No scan crop available for this submission."},
                status=status.HTTP_404_NOT_FOUND,
            )

        png = render_mark_crop(image_bytes, template, target_id)
        if png is None:
            return Response(
                {"detail": "Could not render a crop for this mark."},
                status=status.HTTP_404_NOT_FOUND,
            )
        response = HttpResponse(png, content_type="image/png")
        response["Content-Disposition"] = f'inline; filename="omr-{target_id}.png"'
        return response

    @action(detail=True, methods=["patch"], url_path="materials/(?P<material_id>[^/.]+)/toggle")
    def toggle_material(self, request, pk=None, material_id=None):
        """Toggle whether a material was used in this work order."""
        work_order = self.get_object()
        try:
            usage = work_order.material_usage.get(id=material_id)
        except WorkOrderMaterialUsage.DoesNotExist:
            return Response(
                {"detail": "Material usage record not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        was_used = request.data.get("was_used")
        if was_used is None:
            return Response(
                {"detail": "was_used is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        usage.was_used = bool(was_used)
        usage.save(update_fields=["was_used"])
        return Response(WorkOrderMaterialUsageSerializer(usage).data)


class MaintenanceItemViewSet(viewsets.ModelViewSet):
    """API endpoint for asset maintenance items (PM tasks)."""

    queryset = (
        MaintenanceItem.objects.prefetch_related("materials", "tasks").select_related("asset").all()
    )
    serializer_class = MaintenanceItemSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        queryset = super().get_queryset()
        asset_id = self.request.query_params.get("asset")
        if asset_id:
            queryset = queryset.filter(asset_id=asset_id)
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == "true")
        return queryset

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def complete(self, request, pk=None):
        """Log completion of a maintenance task and update its last_completed_at."""
        item = self.get_object()
        data = request.data.copy()
        data["maintenance_item"] = str(item.id)

        serializer = MaintenanceLogSerializer(data=data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        log = serializer.save(completed_by=request.user)

        # Update the item's last_completed_at timestamp
        item.last_completed_at = log.completed_at
        item.save(update_fields=["last_completed_at"])

        return Response(MaintenanceLogSerializer(log).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"])
    def due_this_week(self, request):
        """Return maintenance items due within the next 7 days."""
        now = timezone.now()
        week_out = now + timedelta(days=7)
        items = []
        qs = (
            self.get_queryset()
            .filter(is_active=True, interval_days__isnull=False)
            .select_related("asset__location")
        )
        for item in qs:
            next_due = item.next_due_at
            if next_due is None or next_due <= week_out:
                items.append(item)
        serializer = MaintenanceItemSerializer(items, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"])
    def due_this_month(self, request):
        """Return maintenance items due within the next 30 days."""
        now = timezone.now()
        month_out = now + timedelta(days=30)
        items = []
        qs = (
            self.get_queryset()
            .filter(is_active=True, interval_days__isnull=False)
            .select_related("asset__location")
        )
        for item in qs:
            next_due = item.next_due_at
            if next_due is None or next_due <= month_out:
                items.append(item)
        serializer = MaintenanceItemSerializer(items, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def check_material_stock(self, request, pk=None):
        """
        Return low-stock alerts for materials on this maintenance item.

        Emits one alert per MaintenanceMaterial that is linked to an InventoryItem
        whose current_stock is below its minimum_stock threshold.
        """
        item = self.get_object()
        alerts = []
        materials = item.materials.select_related("inventory_item").all()
        for material in materials:
            inv = material.inventory_item
            if inv is None:
                continue
            # Retired items are phased out — never emit a low-stock alert.
            if inv.is_retired:
                continue
            if inv.current_stock >= inv.minimum_stock:
                continue
            alerts.append(
                {
                    "material_id": str(material.id),
                    "item_id": str(inv.id),
                    "name": inv.name,
                    "current": inv.current_stock,
                    "minimum": inv.minimum_stock,
                    "reorder_qty": inv.reorder_quantity,
                }
            )
        return Response({"low_stock_alerts": alerts})

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def clone(self, request, pk=None):
        """Clone this maintenance item (with tasks and materials) onto another asset."""
        source = self.get_object()
        target_asset_id = request.data.get("target_asset_id")
        if not target_asset_id:
            return Response(
                {"detail": "target_asset_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            target_asset = Asset.objects.get(pk=target_asset_id)
        except (Asset.DoesNotExist, ValueError, TypeError, DjangoValidationError):
            return Response(
                {"detail": "Target asset not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        with transaction.atomic():
            cloned = MaintenanceItem.objects.create(
                asset=target_asset,
                title=source.title,
                description=source.description,
                instructions=source.instructions,
                estimated_time_minutes=source.estimated_time_minutes,
                estimated_cost=source.estimated_cost,
                interval_days=source.interval_days,
                is_active=source.is_active,
                # last_completed_at intentionally left null — fresh template
            )
            for task in source.tasks.order_by("order", "title"):
                MaintenanceTask.objects.create(
                    maintenance_item=cloned,
                    order=task.order,
                    title=task.title,
                    description=task.description,
                    is_required=task.is_required,
                )
            for mat in source.materials.all():
                MaintenanceMaterial.objects.create(
                    maintenance_item=cloned,
                    name=mat.name,
                    quantity=mat.quantity,
                    unit=mat.unit,
                    estimated_cost_per_unit=mat.estimated_cost_per_unit,
                    notes=mat.notes,
                )

        serializer = MaintenanceItemSerializer(cloned, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def generate_work_order(self, request, pk=None):
        """Create a work order for this maintenance item, pre-populated with tasks and materials."""
        item = self.get_object()

        with transaction.atomic():
            due_date = request.data.get("due_date") or (
                item.next_due_at.date() if item.next_due_at else None
            )
            wo = WorkOrder.objects.create(
                maintenance_item=item,
                due_date=due_date,
                notes=request.data.get("notes", ""),
            )

            # Create task completion records for all tasks
            tasks = list(item.tasks.order_by("order", "title"))
            for task in tasks:
                WorkOrderTaskCompletion.objects.create(
                    work_order=wo,
                    task=task,
                    task_title=task.title,
                    task_order=task.order,
                    is_required=task.is_required,
                )

            # Create material usage records for all materials
            materials = list(item.materials.all())
            for mat in materials:
                WorkOrderMaterialUsage.objects.create(
                    work_order=wo,
                    material=mat,
                    material_name=mat.name,
                    quantity_planned=mat.quantity,
                    unit=mat.unit,
                )

        serializer = WorkOrderSerializer(wo, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated])
    def generate_work_orders_bulk(self, request):
        """Generate work orders for all overdue or due-this-week maintenance items."""
        now = timezone.now()
        week_out = now + timedelta(days=7)
        created = []
        qs = self.get_queryset().filter(is_active=True, interval_days__isnull=False)

        with transaction.atomic():
            for item in qs:
                next_due = item.next_due_at
                is_due = next_due is None or next_due <= week_out

                if not is_due:
                    continue

                # Skip if there's already an open/in-progress WO for this item
                existing = item.work_orders.filter(
                    status__in=[WorkOrder.STATUS_OPEN, WorkOrder.STATUS_IN_PROGRESS]
                ).exists()
                if existing:
                    continue

                due_date = next_due.date() if next_due else now.date()
                wo = WorkOrder.objects.create(
                    maintenance_item=item,
                    due_date=due_date,
                )
                for task in item.tasks.order_by("order", "title"):
                    WorkOrderTaskCompletion.objects.create(
                        work_order=wo,
                        task=task,
                        task_title=task.title,
                        task_order=task.order,
                        is_required=task.is_required,
                    )
                for mat in item.materials.all():
                    WorkOrderMaterialUsage.objects.create(
                        work_order=wo,
                        material=mat,
                        material_name=mat.name,
                        quantity_planned=mat.quantity,
                        unit=mat.unit,
                    )
                created.append(str(wo.id))

        return Response(
            {"created": len(created), "work_order_ids": created},
            status=status.HTTP_201_CREATED,
        )


class MaintenanceDashboardViewSet(viewsets.ViewSet):
    """Aggregated maintenance overview: scheduled PM, unscheduled work, costs."""

    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["get"])
    def dashboard(self, request):
        """Return scheduled PM, open unscheduled work, and cost rollups.

        Cost calculation mirrors AssetReportViewSet.tco: for each completed work
        order, scheduled cost comes from MaintenanceItem.estimated_cost when the
        item has interval_days; unscheduled cost is the sum of used materials
        (quantity_planned * material.estimated_cost_per_unit). Per-period buckets
        filter on completed_at; the by_asset rollup uses the trailing 90 days
        and reports days_in_maintenance_90d the same way TCO does.
        """
        from django.db.models import Prefetch

        now = timezone.now()
        today = now.date()
        week_start_date = today - timedelta(days=today.weekday())
        month_start_date = today.replace(day=1)
        year_start_date = today.replace(month=1, day=1)
        window_start_date = today - timedelta(days=90)

        active_items = (
            MaintenanceItem.objects.filter(is_active=True, interval_days__isnull=False)
            .select_related("asset")
            .order_by("asset__name", "title")
        )
        scheduled_pm = []
        for item in active_items:
            next_due = item.next_due_at
            days_until = None
            if next_due is not None:
                days_until = (next_due.date() - today).days
            scheduled_pm.append(
                {
                    "asset_id": str(item.asset_id),
                    "asset_name": item.asset.name,
                    "maintenance_item_id": str(item.id),
                    "title": item.title,
                    "interval_days": item.interval_days,
                    "next_due": next_due.isoformat() if next_due is not None else None,
                    "days_until": days_until,
                    "last_completed_at": (
                        item.last_completed_at.isoformat()
                        if item.last_completed_at is not None
                        else None
                    ),
                    "is_overdue": item.is_overdue,
                }
            )
        scheduled_pm.sort(
            key=lambda r: (
                r["days_until"] is None,
                r["days_until"] if r["days_until"] is not None else 0,
            )
        )

        open_statuses = [
            WorkOrder.STATUS_OPEN,
            WorkOrder.STATUS_IN_PROGRESS,
            WorkOrder.STATUS_BLOCKED,
        ]
        unscheduled_qs = (
            WorkOrder.objects.filter(
                status__in=open_statuses,
                maintenance_item__interval_days__isnull=True,
            )
            .select_related("maintenance_item__asset")
            .order_by("created_at")
        )
        unscheduled = [
            {
                "workorder_id": str(wo.id),
                "short_id": wo.short_id,
                "asset_id": str(wo.maintenance_item.asset_id),
                "asset_name": wo.maintenance_item.asset.name,
                "problem": wo.maintenance_item.title,
                "opened_at": wo.created_at.isoformat(),
                "status": wo.status,
            }
            for wo in unscheduled_qs
        ]

        completed_qs = (
            WorkOrder.objects.filter(
                status=WorkOrder.STATUS_COMPLETED,
                completed_at__isnull=False,
            )
            .select_related("maintenance_item")
            .prefetch_related("material_usage__material")
        )

        period_starts = {
            "today": today,
            "this_week": week_start_date,
            "this_month": month_start_date,
            "this_year": year_start_date,
        }
        per_period = {key: Decimal("0.00") for key in period_starts}
        per_period["all_time"] = Decimal("0.00")

        for wo in completed_qs:
            mi = wo.maintenance_item
            if mi.interval_days is not None:
                cost = mi.estimated_cost or Decimal("0.00")
            else:
                cost = Decimal("0.00")
                for usage in wo.material_usage.all():
                    if usage.was_used and usage.material is not None:
                        cost += usage.quantity_planned * usage.material.estimated_cost_per_unit
            if cost == 0:
                continue
            completed_date = wo.completed_at.date()
            per_period["all_time"] += cost
            for key, start in period_starts.items():
                if completed_date >= start:
                    per_period[key] += cost

        assets = Asset.objects.all().prefetch_related(
            Prefetch(
                "maintenance_items__work_orders",
                queryset=WorkOrder.objects.prefetch_related("material_usage__material"),
            )
        )
        by_asset = []
        for asset in assets:
            days_set: set = set()
            total_cost = Decimal("0.00")
            for mi in asset.maintenance_items.all():
                for wo in mi.work_orders.all():
                    wo_start = wo.created_at.date()
                    wo_end = wo.completed_at.date() if wo.completed_at else today
                    span_start = max(wo_start, window_start_date)
                    span_end = min(wo_end, today)
                    if span_start <= span_end:
                        d = span_start
                        while d <= span_end:
                            days_set.add(d)
                            d += timedelta(days=1)
                    if (
                        wo.status == WorkOrder.STATUS_COMPLETED
                        and wo.completed_at is not None
                        and window_start_date <= wo.completed_at.date() <= today
                    ):
                        if mi.interval_days is not None:
                            total_cost += mi.estimated_cost or Decimal("0.00")
                        else:
                            for usage in wo.material_usage.all():
                                if usage.was_used and usage.material is not None:
                                    total_cost += (
                                        usage.quantity_planned
                                        * usage.material.estimated_cost_per_unit
                                    )
            if asset.status == Asset.MAINTENANCE:
                days_set.add(today)
            if not days_set and total_cost == 0:
                continue
            by_asset.append(
                {
                    "asset_id": str(asset.id),
                    "asset_name": asset.name,
                    "total_cost": str(total_cost.quantize(Decimal("0.01"))),
                    "days_in_maintenance_90d": len(days_set),
                }
            )
        by_asset.sort(key=lambda r: Decimal(r["total_cost"]), reverse=True)

        return Response(
            {
                "scheduled_pm": scheduled_pm,
                "unscheduled": unscheduled,
                "costs": {
                    "per_period": {
                        key: str(value.quantize(Decimal("0.01")))
                        for key, value in per_period.items()
                    },
                    "by_asset": by_asset,
                },
            }
        )

    @action(detail=False, methods=["get"], url_path="active")
    def active_work_orders(self, request):
        """Unioned active maintenance list: WorkOrders + open Problems.

        Surfaces every item that needs attention from the maintenance team
        in a single feed: open WorkOrders, AssetProblems not yet promoted to
        a WO, and LocationProblems not yet promoted to a WO. Each row carries
        a ``kind`` discriminator so the frontend can render appropriate CTAs.
        """
        open_wo_statuses = [
            WorkOrder.STATUS_OPEN,
            WorkOrder.STATUS_IN_PROGRESS,
            WorkOrder.STATUS_BLOCKED,
        ]
        open_problem_statuses = [
            AssetProblem.REPORTED,
            AssetProblem.IN_PROGRESS,
        ]
        open_location_problem_statuses = [
            LocationProblem.REPORTED,
            LocationProblem.IN_PROGRESS,
        ]

        rows = []

        for wo in (
            WorkOrder.objects.filter(status__in=open_wo_statuses)
            .select_related("maintenance_item__asset")
            .order_by("-created_at")
        ):
            rows.append(
                {
                    "kind": "work_order",
                    "id": str(wo.id),
                    "short_id": wo.short_id,
                    "title": wo.maintenance_item.title,
                    "status": wo.status,
                    "status_display": wo.get_status_display(),
                    "asset_id": str(wo.maintenance_item.asset_id),
                    "asset_name": wo.maintenance_item.asset.name,
                    "location_id": None,
                    "location_name": None,
                    "severity": None,
                    "due_date": wo.due_date.isoformat() if wo.due_date else None,
                    "opened_at": wo.created_at.isoformat(),
                }
            )

        # AssetProblem has no FK to WorkOrder, so we surface every open report.
        for ap in (
            AssetProblem.objects.filter(status__in=open_problem_statuses)
            .select_related("asset")
            .order_by("-created_at")
        ):
            rows.append(
                {
                    "kind": "asset_problem",
                    "id": str(ap.id),
                    "short_id": str(ap.id)[:8].upper(),
                    "title": ap.description[:80],
                    "status": ap.status,
                    "status_display": ap.get_status_display(),
                    "asset_id": str(ap.asset_id),
                    "asset_name": ap.asset.name,
                    "location_id": None,
                    "location_name": None,
                    "severity": None,
                    "due_date": None,
                    "opened_at": ap.created_at.isoformat(),
                }
            )

        for lp in (
            LocationProblem.objects.filter(
                status__in=open_location_problem_statuses,
                work_order__isnull=True,
                third_party_work_order__isnull=True,
            )
            .select_related("location")
            .order_by("-reported_at")
        ):
            rows.append(
                {
                    "kind": "location_problem",
                    "id": str(lp.id),
                    "short_id": str(lp.id)[:8].upper(),
                    "title": lp.description[:80],
                    "status": lp.status,
                    "status_display": lp.get_status_display(),
                    "asset_id": None,
                    "asset_name": None,
                    "location_id": lp.location_id,
                    "location_name": lp.location.name,
                    "severity": lp.severity,
                    "due_date": None,
                    "opened_at": lp.reported_at.isoformat(),
                }
            )

        rows.sort(key=lambda r: r["opened_at"], reverse=True)
        return Response({"results": rows, "count": len(rows)})


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
        from datetime import datetime, timedelta

        from django.db.models import Avg, Count, Sum

        try:
            from forgekey.models import DeviceUsage

            # Get date range from query params (default: last 30 days)
            start_date_str = request.query_params.get("start_date")
            end_date_str = request.query_params.get("end_date")

            if start_date_str and end_date_str:
                try:
                    start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                    end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
                except ValueError:
                    # Fall back to default if date parsing fails
                    start_date = (timezone.now() - timedelta(days=30)).date()
                    end_date = timezone.now().date()
            else:
                # Fall back to days parameter for backward compatibility
                days = int(request.query_params.get("days", 30))
                start_date = (timezone.now() - timedelta(days=days)).date()
                end_date = timezone.now().date()

            # Get usage statistics per asset
            usage_stats = (
                DeviceUsage.objects.filter(
                    started_at__date__gte=start_date,
                    started_at__date__lte=end_date,
                )
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
    def tco(self, request):
        """Per-asset Total Cost of Ownership over the last 90 days.

        Returns one row per asset with maintenance_days_last_90 (distinct calendar
        days where any work order overlapped the window, plus today if the asset
        is currently in MAINTENANCE status), bucketed cost components, and the
        tco total. Sorted by total_maintenance_cost_90d DESC.

        Cost components:
        - scheduled_maintenance_cost / unscheduled_maintenance_cost: preventive
          maintenance via internal WorkOrder + WorkOrderMaterialUsage.
        - vendor_maintenance_cost: third-party WO spend allocated to this asset
          via ThirdPartyWorkOrderAsset.allocated_cost (only closed records, since
          earlier statuses don't have allocated_cost materialized yet).
        - preventive_maintenance_cost: scheduled + unscheduled (alias).
        - total_maintenance_cost_90d: preventive + vendor (the headline figure).
        - tco: scheduled + unscheduled + repair (legacy; preserved for callers
          that haven't migrated to the blended figure).
        - repair_cost: reserved for future use (AssetProblem doesn't carry cost).
        """
        from django.db.models import Prefetch

        from maintenance_orders.models import ThirdPartyWorkOrder, ThirdPartyWorkOrderAsset

        from .serializers import AssetTcoReportSerializer

        today = timezone.now().date()
        window_start = today - timedelta(days=90)

        vendor_link_qs = ThirdPartyWorkOrderAsset.objects.select_related("work_order").filter(
            work_order__status=ThirdPartyWorkOrder.STATUS_CLOSED,
            work_order__closed_at__date__gte=window_start,
            work_order__closed_at__date__lte=today,
        )

        assets = Asset.objects.all().prefetch_related(
            Prefetch(
                "maintenance_items__work_orders",
                queryset=WorkOrder.objects.prefetch_related("material_usage__material"),
            ),
            Prefetch(
                "third_party_work_order_links",
                queryset=vendor_link_qs,
                to_attr="_tco_vendor_links",
            ),
        )

        rows = []
        for asset in assets:
            days_set: set = set()
            scheduled = Decimal("0.00")
            unscheduled = Decimal("0.00")

            for mi in asset.maintenance_items.all():
                for wo in mi.work_orders.all():
                    wo_start = wo.created_at.date()
                    wo_end = wo.completed_at.date() if wo.completed_at else today
                    span_start = max(wo_start, window_start)
                    span_end = min(wo_end, today)
                    if span_start <= span_end:
                        d = span_start
                        while d <= span_end:
                            days_set.add(d)
                            d += timedelta(days=1)

                    if (
                        wo.status == WorkOrder.STATUS_COMPLETED
                        and wo.completed_at is not None
                        and window_start <= wo.completed_at.date() <= today
                    ):
                        if mi.interval_days is not None:
                            scheduled += mi.estimated_cost or Decimal("0.00")
                        else:
                            for usage in wo.material_usage.all():
                                if usage.was_used and usage.material is not None:
                                    unscheduled += (
                                        usage.quantity_planned
                                        * usage.material.estimated_cost_per_unit
                                    )

            vendor = Decimal("0.00")
            for link in getattr(asset, "_tco_vendor_links", []):
                if link.allocated_cost is not None:
                    vendor += link.allocated_cost
                    wo_closed = link.work_order.closed_at
                    if wo_closed is not None:
                        days_set.add(wo_closed.date())

            if asset.status == Asset.MAINTENANCE:
                days_set.add(today)

            repair = Decimal("0.00")
            tco_total = scheduled + unscheduled + repair
            preventive_total = scheduled + unscheduled
            total_90d = preventive_total + vendor

            rows.append(
                {
                    "asset_id": str(asset.id),
                    "asset_name": asset.name,
                    "asset_tag": asset.asset_tag or "",
                    "maintenance_days_last_90": len(days_set),
                    "scheduled_maintenance_cost": scheduled,
                    "unscheduled_maintenance_cost": unscheduled,
                    "repair_cost": repair,
                    "tco": tco_total,
                    "preventive_maintenance_cost": preventive_total,
                    "vendor_maintenance_cost": vendor,
                    "total_maintenance_cost_90d": total_90d,
                }
            )

        rows.sort(key=lambda r: r["total_maintenance_cost_90d"], reverse=True)
        serializer = AssetTcoReportSerializer(rows, many=True)
        return Response(serializer.data)

    @staticmethod
    def _supplies_window(request):
        """Parse ``start_date``/``end_date`` query params for supplies_used.

        Mirrors ``utilization``/``tco`` windowing: both params must be present
        and valid ``YYYY-MM-DD`` strings, otherwise it falls back to the last
        30 days. Returns ``(start_date, end_date)`` as ``date`` objects.
        """
        from datetime import datetime

        start_date_str = request.query_params.get("start_date")
        end_date_str = request.query_params.get("end_date")
        if start_date_str and end_date_str:
            try:
                start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
                return start_date, end_date
            except ValueError:
                pass
        start_date = (timezone.now() - timedelta(days=30)).date()
        end_date = timezone.now().date()
        return start_date, end_date

    @action(detail=False, methods=["get"])
    def supplies_used(self, request):
        """Historical supplies used per asset over a date window.

        Merges two sources into one flat, per-asset-labeled row list. Every row
        carries the common keys ``asset_id``, ``asset_name``, ``source``,
        ``item_name`` and ``used_at`` (ISO-8601); the remaining keys are
        source-specific:

        - ``source == "serialized"`` — a serial-numbered unit put into service
          on, or consumed by, the asset. Sourced from ``ComponentUsageEvent``
          rows tied to an asset with ``action`` in ``install``/``consume``,
          windowed by the event timestamp ``at``. Extra keys: ``serial_number``,
          ``action``, ``action_display``, ``actor``.
        - ``source == "consumable"`` — a bulk maintenance material actually used
          while closing a preventive-maintenance work order. Sourced from
          ``WorkOrderMaterialUsage`` rows with ``was_used=True``, reached via
          ``asset -> maintenance_items -> work_orders -> material_usage`` and
          windowed by the work order's ``completed_at`` date (the same field
          ``tco`` uses). Extra keys: ``quantity`` (planned qty), ``unit``,
          ``work_order_id``, ``estimated_cost`` (``quantity`` ×
          ``material.estimated_cost_per_unit``; null if the material was
          deleted after the work order was created).

        Query params ``start_date``/``end_date`` (``YYYY-MM-DD``) default to the
        last 30 days. Rows are sorted by ``asset_name`` then ``used_at``.
        """
        from django.db.models import Prefetch

        start_date, end_date = self._supplies_window(request)

        rows = []

        # Serialized usage: ComponentUsageEvent tied to an asset. Only install
        # and consume count as "put into service on / used up by" the asset —
        # receive/remove/retire/dispose are stock or teardown events.
        events = ComponentUsageEvent.objects.filter(
            asset__isnull=False,
            action__in=[
                SerializedComponent.ACTION_INSTALL,
                SerializedComponent.ACTION_CONSUME,
            ],
            at__date__gte=start_date,
            at__date__lte=end_date,
        ).select_related("asset", "component", "component__item", "actor")
        for event in events:
            rows.append(
                {
                    "asset_id": str(event.asset_id),
                    "asset_name": event.asset.name,
                    "source": "serialized",
                    "item_name": event.component.item.name,
                    "serial_number": event.component.serial_number,
                    "action": event.action,
                    "action_display": event.get_action_display(),
                    "used_at": event.at,
                    "actor": event.actor.username if event.actor else None,
                }
            )

        # Consumable usage: materials marked used on a completed PM work order.
        # Reuse tco's asset -> maintenance_items -> work_orders -> material_usage
        # prefetch traversal to avoid N+1.
        assets = Asset.objects.all().prefetch_related(
            Prefetch(
                "maintenance_items__work_orders",
                queryset=WorkOrder.objects.prefetch_related("material_usage__material"),
            ),
        )
        for asset in assets:
            for mi in asset.maintenance_items.all():
                for wo in mi.work_orders.all():
                    if wo.status != WorkOrder.STATUS_COMPLETED or wo.completed_at is None:
                        continue
                    completed_date = wo.completed_at.date()
                    if not (start_date <= completed_date <= end_date):
                        continue
                    for usage in wo.material_usage.all():
                        if not usage.was_used:
                            continue
                        estimated_cost = None
                        if usage.material is not None:
                            estimated_cost = str(
                                (
                                    usage.quantity_planned * usage.material.estimated_cost_per_unit
                                ).quantize(Decimal("0.01"))
                            )
                        rows.append(
                            {
                                "asset_id": str(asset.id),
                                "asset_name": asset.name,
                                "source": "consumable",
                                "item_name": usage.material_name,
                                "quantity": str(usage.quantity_planned),
                                "unit": usage.unit,
                                "used_at": wo.completed_at,
                                "work_order_id": str(wo.id),
                                "estimated_cost": estimated_cost,
                            }
                        )

        # Sort on the aware datetimes, then serialize used_at to ISO-8601.
        rows.sort(key=lambda r: (r["asset_name"], r["used_at"]))
        for row in rows:
            row["used_at"] = row["used_at"].isoformat()

        return Response(rows)

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
        elif report_type == "tco":
            response = self.tco(request)
            data = response.data

            response_obj = HttpResponse(content_type="text/csv")
            response_obj["Content-Disposition"] = 'attachment; filename="assets_tco.csv"'

            writer = csv.DictWriter(
                response_obj,
                fieldnames=[
                    "asset_name",
                    "asset_tag",
                    "maintenance_days_last_90",
                    "scheduled_maintenance_cost",
                    "unscheduled_maintenance_cost",
                    "repair_cost",
                    "tco",
                ],
            )
            writer.writeheader()
            for row in data:
                writer.writerow(
                    {
                        "asset_name": row["asset_name"],
                        "asset_tag": row["asset_tag"],
                        "maintenance_days_last_90": row["maintenance_days_last_90"],
                        "scheduled_maintenance_cost": row["scheduled_maintenance_cost"],
                        "unscheduled_maintenance_cost": row["unscheduled_maintenance_cost"],
                        "repair_cost": row["repair_cost"],
                        "tco": row["tco"],
                    }
                )

            return response_obj
        elif report_type == "supplies_used":
            response = self.supplies_used(request)
            data = response.data

            response_obj = HttpResponse(content_type="text/csv")
            response_obj["Content-Disposition"] = 'attachment; filename="assets_supplies_used.csv"'

            # Union of both source shapes; source-specific columns are left
            # blank on rows where they do not apply.
            fieldnames = [
                "asset_id",
                "asset_name",
                "source",
                "item_name",
                "serial_number",
                "action",
                "action_display",
                "quantity",
                "unit",
                "used_at",
                "work_order_id",
                "estimated_cost",
                "actor",
            ]
            writer = csv.DictWriter(response_obj, fieldnames=fieldnames)
            writer.writeheader()
            for row in data:
                writer.writerow(
                    {key: ("" if row.get(key) is None else row.get(key)) for key in fieldnames}
                )

            return response_obj

        return error_response(ErrorCode.VALIDATION_FAILED, "Invalid report type")


# ─────────────────────────────────────────────────────────────────────────────
# Postmark inbound webhook: emailed completed work-order PDFs
# ─────────────────────────────────────────────────────────────────────────────


def _pick_submission_attachment(attachments):
    """Return (filename, raw_bytes, is_image) for the first usable attachment.

    Accepts a PDF (born-digital form OR scanned form) or a directly-attached
    scan image (JPG/PNG). PDFs win over images when both are present, since a
    born-digital PDF carries the richer AcroForm layer. Returns
    ``(None, None, False)`` when nothing usable is attached.
    """
    import base64

    image_fallback = None
    for att in attachments or []:
        name = att.get("Name", "") or ""
        lname = name.lower()
        content_type = (att.get("ContentType") or "").lower()
        is_pdf = content_type == "application/pdf" or lname.endswith(".pdf")
        is_image = content_type in {"image/jpeg", "image/jpg", "image/png"} or lname.endswith(
            (".jpg", ".jpeg", ".png")
        )
        if not (is_pdf or is_image):
            continue
        raw = att.get("Content") or ""
        try:
            data = base64.b64decode(raw)
        except (ValueError, TypeError):
            continue
        if is_pdf:
            return name or "work-order.pdf", data, False
        if image_fallback is None:
            image_fallback = (name or "work-order-scan.png", data, True)
    if image_fallback is not None:
        return image_fallback
    return None, None, False


@api_view(["POST"])
@permission_classes([AllowAny])
def postmark_inbound_work_order(request):
    """
    Inbound webhook for Postmark: accepts a JSON body describing an email with
    a completed work-order PDF attachment, stores the submission, and applies
    the embedded checkbox state to the referenced WorkOrder.

    The endpoint is intentionally unauthenticated (Postmark posts without any
    standard auth scheme), but is gated by a shared secret delivered in the
    `X-Postmark-Webhook-Token` header or `?token=` query string. The secret
    is configured via the `POSTMARK_INBOUND_TOKEN` env var.
    """
    from django.conf import settings as django_settings
    from django.core.files.base import ContentFile

    from .services.work_order_ingest import (
        apply_submission,
        detect_submission_kind,
        looks_like_scan,
    )

    expected = getattr(django_settings, "POSTMARK_INBOUND_TOKEN", "") or ""
    if not expected:
        # Refuse to run without a configured secret — prevents accidental open
        # ingestion when the env var is missing in production.
        return Response(
            {"detail": "Postmark inbound is not configured."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    provided = request.headers.get("X-Postmark-Webhook-Token") or request.GET.get("token") or ""
    if provided != expected:
        return error_response(
            ErrorCode.PERMISSION_DENIED,
            "Forbidden.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    payload = request.data if isinstance(request.data, dict) else {}
    message_id = payload.get("MessageID", "") or ""
    if message_id:
        existing = WorkOrderSubmission.objects.filter(postmark_message_id=message_id).first()
        if existing:
            return Response(
                {
                    "id": str(existing.id),
                    "status": existing.status,
                    "duplicate": True,
                },
                status=status.HTTP_200_OK,
            )

    filename, pdf_bytes, is_image = _pick_submission_attachment(payload.get("Attachments") or [])
    if not pdf_bytes:
        return Response(
            {"detail": "No PDF or scan-image attachment found on inbound message."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    subject = (payload.get("Subject") or "")[:500]
    kind = detect_submission_kind(pdf_bytes, subject=subject)
    is_scan = looks_like_scan(pdf_bytes, is_image=is_image)

    submission = WorkOrderSubmission(
        kind=kind,
        from_email=(payload.get("FromFull") or {}).get("Email") or payload.get("From") or "",
        subject=subject,
        postmark_message_id=message_id[:200],
        status=WorkOrderSubmission.STATUS_RECEIVED,
        source=(WorkOrderSubmission.SOURCE_SCAN if is_scan else WorkOrderSubmission.SOURCE_EMAIL),
    )
    submission.attachment.save(filename, ContentFile(pdf_bytes), save=False)
    submission.save()

    try:
        apply_submission(submission)
    except Exception:  # noqa: BLE001 - webhook must never 500 back to Postmark
        import logging

        logging.getLogger(__name__).exception(
            "Failed to apply inbound work order submission %s", submission.id
        )
        submission.refresh_from_db()

    return Response(
        {
            "id": str(submission.id),
            "kind": submission.kind,
            "status": submission.status,
            "work_order_id": (str(submission.work_order_id) if submission.work_order_id else None),
            "third_party_work_order_id": (
                str(submission.third_party_work_order_id)
                if submission.third_party_work_order_id
                else None
            ),
            "parse_error": submission.parse_error or None,
        },
        status=status.HTTP_200_OK,
    )


def _user_can_reconcile_item(user, item):
    """Return True if `user` may submit reconciliation rows for `item`.

    Staff, superusers, and SIG admins of the item's owning_group are allowed.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    from membership.utils import is_sig_admin

    group = getattr(item, "owning_group", None)
    if group is None:
        return False
    return is_sig_admin(user, group)


def _apply_reconciliation_row(user, item, actual_count, reason, notes="", skip_reorder=False):
    """Apply a single reconciliation row. Caller owns transaction + permission check.

    Returns (StockReconciliation, reorder_created_bool).
    """
    projected = int(item.current_stock)
    actual = int(actual_count)
    delta = actual - projected
    item.current_stock = actual
    item.save(update_fields=["current_stock", "updated_at"])

    reconciliation = StockReconciliation.objects.create(
        item=item,
        projected_count=projected,
        actual_count=actual,
        delta=delta,
        reason=reason,
        notes=notes or "",
        reconciled_by=user,
    )

    reorder_created = False
    # Retired items are phased out: reconciliation must never auto-create a
    # ReorderRequest for them, even when the counted stock is at/below minimum.
    if not skip_reorder and not item.is_retired and actual <= item.minimum_stock:
        from reorder_queue.models import ReorderRequest

        requested_by = (user.get_full_name() or user.username).strip()
        reorder_quantity = item.reorder_quantity or 1
        reorder = ReorderRequest.objects.create(
            item=item,
            quantity=reorder_quantity,
            requested_by=requested_by,
            request_notes=(
                "Auto-created by stock reconciliation "
                f"(actual={actual}, minimum={item.minimum_stock})."
            ),
        )
        reconciliation.triggered_reorder = reorder
        reconciliation.save(update_fields=["triggered_reorder"])
        reorder_created = True

    return reconciliation, reorder_created


class InventoryReconciliationViewSet(viewsets.ViewSet):
    """Endpoints for manual stock reconciliation."""

    permission_classes = [IsAuthenticated]

    def list(self, request):
        qs = StockReconciliation.objects.select_related(
            "item", "reconciled_by", "triggered_reorder"
        ).all()
        item_id = request.query_params.get("item")
        reason = request.query_params.get("reason")
        if item_id:
            qs = qs.filter(item_id=item_id)
        if reason:
            qs = qs.filter(reason=reason)
        serializer = StockReconciliationSerializer(qs[:500], many=True)
        return Response(serializer.data)

    def location_grid(self, request, location_id=None):
        """Return the reconciliation grid payload for a location.

        Wired at GET /api/inventory/locations/<location_id>/reconcile/.
        """
        try:
            location = Location.objects.get(pk=location_id)
        except Location.DoesNotExist:
            return Response(
                {"detail": "Location not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        items = (
            InventoryItem.objects.filter(location=location, is_active=True)
            .select_related("owning_group")
            .order_by("name")
        )
        serializer = LocationReconcileItemSerializer(items, many=True)
        return Response(
            {
                "location_id": str(location.pk),
                "location_name": location.name,
                "items": serializer.data,
            }
        )

    @action(detail=False, methods=["post"], url_path="batch")
    def batch(self, request):
        """Submit a batch of reconciliation rows; atomic across the batch."""
        batch_serializer = StockReconciliationBatchSerializer(data=request.data)
        batch_serializer.is_valid(raise_exception=True)
        rows = batch_serializer.validated_data["rows"]

        item_ids = [row["item_id"] for row in rows]
        items = {
            item.pk: item
            for item in InventoryItem.objects.select_for_update().filter(pk__in=item_ids)
        }
        missing = [str(i) for i in item_ids if i not in items]
        if missing:
            return Response(
                {"detail": f"Unknown item(s): {', '.join(missing)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        for row in rows:
            item = items[row["item_id"]]
            if not _user_can_reconcile_item(request.user, item):
                return Response(
                    {"detail": ("You do not have permission to reconcile item " f"{item.name}.")},
                    status=status.HTTP_403_FORBIDDEN,
                )

        created_reconciliations = []
        reorders_created = 0

        try:
            with transaction.atomic():
                for row in rows:
                    item = items[row["item_id"]]
                    reconciliation, reorder_created = _apply_reconciliation_row(
                        request.user,
                        item,
                        row["actual_count"],
                        row["reason"],
                        notes=row.get("notes", "") or "",
                        skip_reorder=bool(row.get("skip_reorder", False)),
                    )
                    if reorder_created:
                        reorders_created += 1
                    created_reconciliations.append(reconciliation)
        except DjangoValidationError as exc:
            return error_response(ErrorCode.VALIDATION_FAILED, str(exc))

        output = StockReconciliationSerializer(created_reconciliations, many=True).data
        return Response(
            {
                "reconciled": len(created_reconciliations),
                "reorders_created": reorders_created,
                "reconciliations": output,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["post"], url_path="upload")
    def upload_csv(self, request):
        """Batch-reconcile via multipart CSV upload.

        Modes:
          - default (all-or-nothing): any row error rolls back the whole batch
            and returns 400 with per-row errors.
          - ?partial=true: bad rows are skipped and reported; valid rows commit.
        """
        file_obj = request.FILES.get("file")
        if not file_obj:
            return Response(
                {"detail": "No file uploaded. Send the CSV under form field 'file'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        partial = str(request.query_params.get("partial", "")).lower() in (
            "1",
            "true",
            "yes",
        )

        valid_reasons = {v for v, _ in StockReconciliation.REASON_CHOICES}

        try:
            decoded = io.TextIOWrapper(file_obj, encoding="utf-8-sig", newline="")
        except Exception as exc:  # pragma: no cover
            return Response(
                {"detail": f"Could not decode file as UTF-8 CSV: {exc}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        reader = csv.DictReader(decoded)
        if not reader.fieldnames:
            return Response(
                {"detail": "CSV is empty or missing header row."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        header_set = {(f or "").strip().lower() for f in reader.fieldnames}
        if "actual_count" not in header_set:
            return Response(
                {"detail": "CSV missing required 'actual_count' column."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if "reason" not in header_set:
            return Response(
                {"detail": "CSV missing required 'reason' column."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if "item_id" not in header_set and "sku" not in header_set:
            return Response(
                {"detail": "CSV must include either 'item_id' or 'sku' column."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        parsed = []
        errors = []
        for row_num, raw in enumerate(reader, start=2):
            row = {
                (k or "").strip().lower(): (v.strip() if isinstance(v, str) else "")
                for k, v in raw.items()
                if k is not None
            }
            item_id = row.get("item_id") or ""
            sku = row.get("sku") or ""
            item = None
            if item_id:
                try:
                    item = InventoryItem.objects.filter(pk=item_id).first()
                except (DjangoValidationError, ValueError):
                    item = None
            if item is None and sku:
                item = InventoryItem.objects.filter(sku=sku).first()
            if item is None:
                errors.append(
                    {
                        "row": row_num,
                        "error": (f"Item not found (item_id={item_id!r}, sku={sku!r})."),
                    }
                )
                continue
            try:
                actual = int(row.get("actual_count") or "")
            except (TypeError, ValueError):
                errors.append(
                    {
                        "row": row_num,
                        "error": "actual_count must be a non-negative integer.",
                    }
                )
                continue
            if actual < 0:
                errors.append({"row": row_num, "error": "actual_count must be >= 0."})
                continue
            reason = (row.get("reason") or "").lower()
            if reason not in valid_reasons:
                errors.append(
                    {
                        "row": row_num,
                        "error": (
                            f"Invalid reason {reason!r}; choose from " f"{sorted(valid_reasons)}."
                        ),
                    }
                )
                continue
            if not _user_can_reconcile_item(request.user, item):
                errors.append(
                    {
                        "row": row_num,
                        "error": (f"Permission denied for item {item.name}."),
                    }
                )
                continue
            skip_flag = (row.get("skip_reorder") or "").lower()
            skip_reorder = skip_flag in ("1", "true", "yes", "y", "t")
            parsed.append(
                {
                    "row": row_num,
                    "item_pk": item.pk,
                    "actual": actual,
                    "reason": reason,
                    "notes": row.get("notes") or "",
                    "skip_reorder": skip_reorder,
                }
            )

        if not parsed and not errors:
            return Response(
                {"detail": "CSV contains no data rows."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not partial and errors:
            return Response(
                {
                    "created": 0,
                    "skipped": len(parsed) + len(errors),
                    "errors": errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        created = 0
        try:
            with transaction.atomic():
                for p in parsed:
                    item = InventoryItem.objects.select_for_update().get(pk=p["item_pk"])
                    _apply_reconciliation_row(
                        request.user,
                        item,
                        p["actual"],
                        p["reason"],
                        notes=p["notes"],
                        skip_reorder=p["skip_reorder"],
                    )
                    created += 1
        except DjangoValidationError as exc:
            return error_response(ErrorCode.VALIDATION_FAILED, str(exc))

        return Response(
            {
                "created": created,
                "skipped": len(errors),
                "errors": errors,
            },
            status=status.HTTP_201_CREATED,
        )

    def location_export(self, request, location_id=None):
        """Return a pre-populated CSV template for offline reconciliation.

        Wired at GET /api/inventory/locations/<location_id>/reconcile/export/.
        """
        try:
            location = Location.objects.get(pk=location_id)
        except Location.DoesNotExist:
            return Response(
                {"detail": "Location not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        items = (
            InventoryItem.objects.filter(location=location, is_active=True)
            .only(
                "id",
                "sku",
                "name",
                "current_stock",
                "minimum_stock",
            )
            .order_by("name")
            .iterator(chunk_size=500)
        )

        header = [
            "item_id",
            "sku",
            "name",
            "projected",
            "minimum_stock",
            "actual_count",
            "reason",
            "notes",
            "skip_reorder",
        ]

        class _Echo:
            def write(self, value):
                return value

        writer = csv.writer(_Echo())

        def _rows():
            yield writer.writerow(header)
            for item in items:
                yield writer.writerow(
                    [
                        str(item.pk),
                        item.sku,
                        item.name,
                        item.current_stock,
                        item.minimum_stock,
                        "",
                        "",
                        "",
                        "",
                    ]
                )

        response = StreamingHttpResponse(_rows(), content_type="text/csv")
        filename = f"reconcile_location_{location.pk}.csv"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class MaintenanceRecordViewSet(viewsets.ModelViewSet):
    """API for backdated/recent maintenance records on assets.

    Permissions:
    - Read: any authenticated user.
    - Create/update: staff, Logistics, or SIG admin (`IsAuthenticatedOrStaffSigAdminWrite`).
    - Delete: staff/superuser only (deletions are auditable but not undoable).
    """

    queryset = MaintenanceRecord.objects.select_related(
        "asset", "vendor", "performed_by_internal", "recorded_by"
    )
    serializer_class = MaintenanceRecordSerializer
    permission_classes = [IsAuthenticatedOrStaffSigAdminWrite]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self):
        qs = super().get_queryset()
        asset_id = self.request.query_params.get("asset")
        if asset_id:
            qs = qs.filter(asset_id=asset_id)
        vendor_id = self.request.query_params.get("vendor")
        if vendor_id:
            qs = qs.filter(vendor_id=vendor_id)
        since = self.request.query_params.get("since")
        if since:
            try:
                qs = qs.filter(completed_on__gte=date.fromisoformat(since))
            except ValueError:
                pass
        until = self.request.query_params.get("until")
        if until:
            try:
                qs = qs.filter(completed_on__lte=date.fromisoformat(until))
            except ValueError:
                pass
        return qs

    def perform_create(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        serializer.save(recorded_by=user)

    def destroy(self, request, *args, **kwargs):
        user = request.user
        if not (user.is_authenticated and (user.is_staff or user.is_superuser)):
            return Response(
                {"detail": "Only staff may delete maintenance records."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().destroy(request, *args, **kwargs)


def _user_can_manage_asset(user, asset) -> bool:
    """True for staff/superuser, or SIG admin of the asset's owning_group.

    Matches the "staff or SIG admin" auth contract used by the
    maintenance + work-order surfaces. Reserving an asset and placing
    it out of service are both treated as administrative operations on
    the asset itself, so they share that gate.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    if asset is None:
        return False
    # asset.is_user_group_admin already handles owning_group=None.
    return bool(asset.is_user_group_admin(user))


class AssetReservationViewSet(viewsets.ModelViewSet):
    """Reservations against an asset for a class / training / event.

    POST creates; DELETE cancels (soft — sets cancelled_at, preserves
    history). PATCH allows editing title/notes/window but re-runs the
    overlap check via clean(). Read access is open to any authenticated
    user; write access is gated by staff or SIG-admin-of-owning-group.
    """

    queryset = AssetReservation.objects.select_related("asset", "reserved_by").all()
    serializer_class = AssetReservationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        asset_id = self.request.query_params.get("asset")
        if asset_id:
            qs = qs.filter(asset_id=asset_id)
        active = self.request.query_params.get("active")
        if active is not None:
            truthy = active.lower() in ("true", "1", "yes")
            now = timezone.now()
            if truthy:
                qs = qs.filter(cancelled_at__isnull=True, ends_at__gt=now)
            else:
                qs = qs.filter(Q(cancelled_at__isnull=False) | Q(ends_at__lte=now))
        current = self.request.query_params.get("current")
        if current is not None and current.lower() in ("true", "1", "yes"):
            now = timezone.now()
            qs = qs.filter(cancelled_at__isnull=True, starts_at__lte=now, ends_at__gt=now)
        return qs

    def perform_create(self, serializer):
        asset = serializer.validated_data.get("asset")
        if not _user_can_manage_asset(self.request.user, asset):
            raise PermissionDenied("Only staff or SIG admins can reserve this asset.")
        instance = AssetReservation(reserved_by=self.request.user, **serializer.validated_data)
        try:
            instance.full_clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_django_to_drf_errors(exc))
        instance.save()
        serializer.instance = instance

    def perform_update(self, serializer):
        instance = serializer.instance
        asset = serializer.validated_data.get("asset", instance.asset)
        if not _user_can_manage_asset(self.request.user, asset):
            raise PermissionDenied("Only staff or SIG admins can edit this reservation.")
        for field, value in serializer.validated_data.items():
            setattr(instance, field, value)
        try:
            instance.full_clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_django_to_drf_errors(exc))
        instance.save()

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if not _user_can_manage_asset(request.user, instance.asset):
            return Response(
                {"detail": "Only staff or SIG admins can cancel this reservation."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if instance.cancelled_at is None:
            instance.cancelled_at = timezone.now()
            instance.save(update_fields=["cancelled_at", "updated_at"])
        return Response(self.get_serializer(instance).data, status=status.HTTP_200_OK)


class AssetOutOfServiceViewSet(viewsets.ModelViewSet):
    """Out-of-service events against an asset.

    POST opens; POST /{id}/restore/ closes (sets restored_at +
    restored_by). The model rejects opening a second OOS while the
    first is still open. Read open to any authenticated user; write
    gated to staff or SIG admin of the asset's owning group.
    """

    queryset = AssetOutOfService.objects.select_related("asset", "placed_by", "restored_by").all()
    serializer_class = AssetOutOfServiceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        asset_id = self.request.query_params.get("asset")
        if asset_id:
            qs = qs.filter(asset_id=asset_id)
        open_only = self.request.query_params.get("open")
        if open_only is not None and open_only.lower() in ("true", "1", "yes"):
            qs = qs.filter(restored_at__isnull=True)
        return qs

    def perform_create(self, serializer):
        asset = serializer.validated_data.get("asset")
        if not _user_can_manage_asset(self.request.user, asset):
            raise PermissionDenied("Only staff or SIG admins can place this asset out of service.")
        instance = AssetOutOfService(placed_by=self.request.user, **serializer.validated_data)
        try:
            instance.full_clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_django_to_drf_errors(exc))
        instance.save()
        serializer.instance = instance

    def perform_update(self, serializer):
        instance = serializer.instance
        asset = serializer.validated_data.get("asset", instance.asset)
        if not _user_can_manage_asset(self.request.user, asset):
            raise PermissionDenied("Only staff or SIG admins can edit this OOS event.")
        for field, value in serializer.validated_data.items():
            setattr(instance, field, value)
        try:
            instance.full_clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_django_to_drf_errors(exc))
        instance.save()

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def restore(self, request, pk=None):
        """Close the OOS — sets restored_at + restored_by."""
        instance = self.get_object()
        if not _user_can_manage_asset(request.user, instance.asset):
            return Response(
                {"detail": "Only staff or SIG admins can restore this asset."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if instance.restored_at is not None:
            return Response(
                self.get_serializer(instance).data,
                status=status.HTTP_200_OK,
            )
        instance.restored_at = timezone.now()
        instance.restored_by = request.user
        instance.save(update_fields=["restored_at", "restored_by", "updated_at"])
        return Response(self.get_serializer(instance).data, status=status.HTTP_200_OK)


def _django_to_drf_errors(exc):
    """Convert a Django ValidationError into a shape DRF can render.

    Pulls .message_dict when fields were named (clean()'s typical
    output), falls back to messages otherwise. Keeps the existing
    error envelope so the frontend gets `{ field: [message] }` rather
    than a raw exception string.
    """
    if hasattr(exc, "message_dict") and exc.message_dict:
        return {k: list(v) for k, v in exc.message_dict.items()}
    return {"detail": list(getattr(exc, "messages", [str(exc)]))}


class SerializedComponentViewSet(viewsets.ModelViewSet):
    """CRUD plus lifecycle actions for individual serial-numbered component units.

    Read is available to any authenticated user; create/update/delete and the
    lifecycle actions (``receive``/``install``/``remove``/``consume``/
    ``retire``/``dispose``) are gated to staff or SIG admins. Each lifecycle
    action validates the transition against the owning item's tracking mode and
    records a ``ComponentUsageEvent``.

    List supports ``?item=``, ``?status=`` and ``?installed_in_asset=`` filters.
    """

    queryset = SerializedComponent.objects.select_related("item", "installed_in_asset").all()
    serializer_class = SerializedComponentSerializer
    permission_classes = [IsAuthenticatedOrStaffSigAdminWrite]

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        item_id = params.get("item")
        if item_id:
            qs = qs.filter(item_id=item_id)
        status_param = params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        asset_id = params.get("installed_in_asset")
        if asset_id:
            qs = qs.filter(installed_in_asset_id=asset_id)
        return qs

    def _run_lifecycle_action(self, request, action_name):
        """Resolve inputs, apply the transition, and return the updated unit."""
        component = self.get_object()

        asset = None
        asset_id = request.data.get("asset") or request.data.get("installed_in_asset")
        if asset_id:
            try:
                asset = Asset.objects.filter(pk=asset_id).first()
            except (DjangoValidationError, ValueError, TypeError):
                asset = None
            if asset is None:
                return Response(
                    {"asset": ["No asset found with the given id."]},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        disposal_reason = (request.data.get("disposal_reason") or "").strip()
        notes = (request.data.get("notes") or "").strip()
        actor = request.user if request.user.is_authenticated else None

        try:
            event = component.apply_action(
                action_name,
                asset=asset,
                disposal_reason=disposal_reason,
                actor=actor,
                notes=notes,
            )
        except DjangoValidationError as exc:
            return Response(_django_to_drf_errors(exc), status=status.HTTP_400_BAD_REQUEST)

        component.refresh_from_db()
        data = self.get_serializer(component).data
        data["event"] = ComponentUsageEventSerializer(event).data
        return Response(data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"])
    def receive(self, request, pk=None):
        """Accession a received unit into stock (``received`` -> ``in_stock``)."""
        return self._run_lifecycle_action(request, SerializedComponent.ACTION_RECEIVE)

    @action(detail=True, methods=["post"])
    def install(self, request, pk=None):
        """Install the unit into an asset (requires ``asset``)."""
        return self._run_lifecycle_action(request, SerializedComponent.ACTION_INSTALL)

    @action(detail=True, methods=["post"])
    def remove(self, request, pk=None):
        """Remove a reusable unit from its asset (``installed`` -> ``removed``)."""
        return self._run_lifecycle_action(request, SerializedComponent.ACTION_REMOVE)

    @action(detail=True, methods=["post"])
    def consume(self, request, pk=None):
        """Mark a consumable unit as used up (``installed`` -> ``consumed``)."""
        return self._run_lifecycle_action(request, SerializedComponent.ACTION_CONSUME)

    @action(detail=True, methods=["post"])
    def retire(self, request, pk=None):
        """Retire a reusable unit from service (-> ``retired``)."""
        return self._run_lifecycle_action(request, SerializedComponent.ACTION_RETIRE)

    @action(detail=True, methods=["post"])
    def dispose(self, request, pk=None):
        """Dispose the unit (requires ``disposal_reason``; -> ``disposed``)."""
        return self._run_lifecycle_action(request, SerializedComponent.ACTION_DISPOSE)

    @action(detail=False, methods=["post"])
    def scan_receive(self, request):
        """Idempotently create-and-receive a scanned unit (scan = received).

        A scanned serial is treated as *received* with no purchase order
        required — serials are unknown until receipt, and batch scanning (web +
        ScanTTY) drives this endpoint. Body: ``{item, serial_number, lot?,
        expiration_date?}``.

        * **First scan** of an ``(item, serial_number)`` pair creates the unit
          (recording ``lot`` / ``expiration_date``) and applies ``receive`` so
          it lands ``in_stock`` (i.e. *available*). Returns 201 with
          ``created: true``.
        * **Re-scan** of the same pair is a no-op that returns the existing unit
          with 200 and ``created: false`` — never a 400 unique-constraint error,
          so double-scans within a batch are tolerated.

        The owning item must be ``is_serialized`` (validated exactly like the
        create path, via ``validate_item``).
        """
        # Reuse the component serializer to validate the item is serialized
        # (validate_item) and to parse/clean lot + expiration_date. We do NOT
        # persist through it, and we drop the auto-generated
        # (item, serial_number) UniqueTogetherValidator: a re-scan must resolve
        # to the existing unit via get_or_create below (idempotent, HTTP 200),
        # not fail is_valid() with a 400 unique-constraint error.
        from rest_framework.validators import UniqueTogetherValidator

        in_serializer = self.get_serializer(
            data={
                "item": request.data.get("item"),
                "serial_number": request.data.get("serial_number"),
                "lot": request.data.get("lot", ""),
                "expiration_date": request.data.get("expiration_date"),
            }
        )
        in_serializer.validators = [
            v for v in in_serializer.validators if not isinstance(v, UniqueTogetherValidator)
        ]
        in_serializer.is_valid(raise_exception=True)
        validated = in_serializer.validated_data
        item = validated["item"]
        serial_number = validated["serial_number"]

        actor = request.user if request.user.is_authenticated else None
        with transaction.atomic():
            component, created = SerializedComponent.objects.get_or_create(
                item=item,
                serial_number=serial_number,
                defaults={
                    "lot": validated.get("lot", ""),
                    "expiration_date": validated.get("expiration_date"),
                },
            )
            if created:
                component.apply_action(SerializedComponent.ACTION_RECEIVE, actor=actor)

        component.refresh_from_db()
        data = self.get_serializer(component).data
        data["created"] = created
        return Response(
            data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class ComponentUsageEventViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only access to the serialized-component usage/audit log.

    Entries are written as a side effect of ``SerializedComponentViewSet``
    lifecycle actions. Supports a ``?component=`` filter and an ``?asset=``
    filter (every serial this machine has used across install/remove/consume/
    retire/dispose).
    """

    queryset = ComponentUsageEvent.objects.select_related("component__item", "asset", "actor").all()
    serializer_class = ComponentUsageEventSerializer
    permission_classes = [IsAuthenticatedOrStaffSigAdminWrite]

    def get_queryset(self):
        qs = super().get_queryset()
        component_id = self.request.query_params.get("component")
        if component_id:
            qs = qs.filter(component_id=component_id)
        asset_id = self.request.query_params.get("asset")
        if asset_id:
            qs = qs.filter(asset_id=asset_id)
        return qs
