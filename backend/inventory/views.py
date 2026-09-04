"""
Views for inventory API.
"""

import csv
import io
import uuid
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models, transaction
from django.db.models import F, Q
from django.http import HttpResponse, StreamingHttpResponse
from django.utils import timezone

from drf_spectacular.utils import OpenApiResponse, extend_schema
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
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.response import Response

from config.api_errors import ErrorCode, error_response
from membership.actor import actor_display
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
    MaintenanceAuditEvent,
    MaintenanceItem,
    MaintenanceLog,
    MaintenanceMaterial,
    MaintenanceRecord,
    MaintenanceTask,
    MaintenanceTool,
    PriceHistory,
    SerializedComponent,
    StockReconciliation,
    Supplier,
    SupplierAgreement,
    UsageLog,
    WorkOrder,
    WorkOrderAttachment,
    WorkOrderLotoCompletion,
    WorkOrderMaterialUsage,
    WorkOrderSubmission,
    WorkOrderTaskCompletion,
    WorkOrderTool,
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
    ItemDeliverySerializer,
    ItemOrderCostSerializer,
    ItemSupplierSerializer,
    KitSerializer,
    KitSummarySerializer,
    LocationProblemSerializer,
    LocationReconcileItemSerializer,
    LocationSerializer,
    MaintenanceItemSerializer,
    MaintenanceLogSerializer,
    MaintenanceMaterialSerializer,
    MaintenanceRecordSerializer,
    MaintenanceTaskSerializer,
    MaintenanceToolSerializer,
    PriceHistorySerializer,
    SerializedComponentSerializer,
    StockReconciliationBatchSerializer,
    StockReconciliationSerializer,
    SupplierAgreementSerializer,
    SupplierDetailSerializer,
    SupplierSerializer,
    UsageLogSerializer,
    WorkOrderAdHocMaterialSerializer,
    WorkOrderAdHocToolSerializer,
    WorkOrderAttachmentSerializer,
    WorkOrderListSerializer,
    WorkOrderLotoCompletionSerializer,
    WorkOrderMaterialUsageSerializer,
    WorkOrderPhotoSerializer,
    WorkOrderSerializer,
    WorkOrderTaskCompletionSerializer,
    WorkOrderToolLocationSerializer,
    WorkOrderToolSerializer,
    WorkOrderValidationSerializer,
)
from .services.packaging import (
    base_reorder_quantity,
    count_at_level,
    count_unit,
    counts_in_packs,
    on_hand_display,
    parse_at_level,
    resolve_base_quantity,
)
from .services.pricing import package_price_of, price_float, unit_price_of
from .services.problem_auto_resolve import resolve_problems_for_work_order
from .services.supplier_selection import item_suppliers_prefetch
from .services.work_order_tools import create_work_order_tools


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

            # The same shape the supplier-detail block serves when a supplier
            # has no deliveries yet: the yardstick must not appear and vanish
            # with the data, and these two endpoints must not disagree about
            # what an empty block looks like either.
            lead_time_stats = {
                "average_lead_time": None,
                "min_lead_time": None,
                "max_lead_time": None,
                "avg_variance_vs_quoted_lead_time_days": None,
                "total_orders": 0,
                "within_quoted_lead_time_pct": None,
                "variance_measured_against": LeadTimeLog.VARIANCE_YARDSTICK,
            }
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
                    # ``is not None``, never truthiness — same reasoning and
                    # same spelling as the supplier-detail block: a perfect
                    # record averages 0.0 and is an answer, not an absence.
                    "average_lead_time": (
                        float(stats["avg_lead_time"])
                        if stats["avg_lead_time"] is not None
                        else None
                    ),
                    "min_lead_time": stats["min_lead_time"],
                    "max_lead_time": stats["max_lead_time"],
                    "avg_variance_vs_quoted_lead_time_days": (
                        float(stats["avg_variance"]) if stats["avg_variance"] is not None else None
                    ),
                    "total_orders": stats["total_orders"],
                    "within_quoted_lead_time_pct": (
                        float(on_time_percentage) if on_time_percentage is not None else None
                    ),
                    # Same yardstick, same keys, same source constant as the
                    # supplier-detail block in ``inventory.serializers`` — these
                    # two endpoints serve the same numbers and must not name two
                    # different promises, empty or not.
                    "variance_measured_against": LeadTimeLog.VARIANCE_YARDSTICK,
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
                        # ``is_known`` / ``is not None``, never truthiness: a
                        # snapshot recording 0.00 is a price this supplier
                        # charged, and a price_change_percentage of exactly 0
                        # is "no change", not "no data" (op-9m2v).
                        "unit_cost": price_float(unit_price_of(ph)),
                        "package_cost": price_float(package_price_of(ph)),
                        "change_type": ph.change_type,
                        "price_change_percentage": (
                            None
                            if ph.price_change_percentage is None
                            else float(ph.price_change_percentage)
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
                "received_orders": purchase_orders.filter(
                    status=PurchaseOrder.Status.RECEIVED
                ).count(),
                "total_spent": (
                    purchase_orders.filter(
                        status=PurchaseOrder.Status.RECEIVED, actual_total__isnull=False
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


class SupplierAgreementViewSet(viewsets.ModelViewSet):
    """API endpoint for supplier purchase/pricing agreements (op-yoos).

    Filterable by ``?supplier=<id>`` and ``?is_active=true|false`` — the
    purchase-order create form asks for one supplier's active agreements.
    """

    queryset = SupplierAgreement.objects.select_related("supplier").all()
    serializer_class = SupplierAgreementSerializer
    # Mirrors SupplierViewSet: agreements are supplier reference data.
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        """Filter agreements by supplier and/or active flag."""
        queryset = super().get_queryset()

        supplier = self.request.query_params.get("supplier")
        if supplier:
            # A non-numeric ?supplier= is a caller mistake, not a server error —
            # answer with an empty page rather than letting the ORM raise.
            try:
                queryset = queryset.filter(supplier_id=int(supplier))
            except (TypeError, ValueError):
                return queryset.none()

        is_active = self.request.query_params.get("is_active")
        if is_active is not None and is_active != "":
            queryset = queryset.filter(is_active=is_active.lower() in ("true", "1", "yes"))

        return queryset


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

        severity = request.data.get("severity") or LocationProblem.Severity.MEDIUM
        valid_sev = {choice for choice, _ in LocationProblem.Severity.choices}
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
            LocationProblem.Severity.HIGH,
            LocationProblem.Severity.URGENT,
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
        InventoryItem.objects.select_related(
            "category", "location", "safety_profile", "count_level"
        )
        .prefetch_related(item_suppliers_prefetch(), "packaging_levels")
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
            # "Which kits supply this cartridge?" is reorder-triage context
            # shown beside stock on the item detail page (op-8n0), so it reads
            # as publicly as the item itself.
            "kits",
        ]:
            return [AllowAny()]
        # Admin actions (create, update, delete)
        return [IsAuthenticated()]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return InventoryItemDetailSerializer
        return InventoryItemSerializer

    def get_queryset(self):
        # Two order-preserving Prefetches kill the default-list reorder_requests
        # N+1 (issue #890). Each mirrors its accessor's exact filter + order so
        # the cached [0]/bool() reads in inventory/models/core.py are
        # byte-identical to the live queries they replace:
        #   * _active_reorder_requests -> get_active_reorder_request /
        #     has_pending_reorder / reorder_status  (status in pending/approved/
        #     ordered, ordered by -requested_at)
        #   * _ordered_reorder_requests -> get_expected_delivery_date, which
        #     orders by -ordered_at (the ORDERING TRAP: sharing one prefetch
        #     would silently return the wrong row).
        # Each Prefetch is a single query regardless of page size (2 total).
        # ReorderRequest is imported locally to avoid a circular import.
        from django.db.models import Prefetch

        from reorder_queue.models import ReorderRequest

        queryset = (
            InventoryItem.objects.select_related(
                "category", "location", "safety_profile", "count_level"
            )
            .prefetch_related(
                # ``packaging_levels`` + the ``count_level`` join keep the
                # unit-of-measure fields (op-hzji) off the per-row path: both the
                # nested chain and ``on_hand_display`` read them from cache.
                item_suppliers_prefetch(),
                "packaging_levels",
                Prefetch(
                    "reorder_requests",
                    queryset=ReorderRequest.objects.filter(
                        status__in=["pending", "approved", "ordered"]
                    ).order_by("-requested_at"),
                    to_attr="_active_reorder_requests",
                ),
                Prefetch(
                    "reorder_requests",
                    queryset=ReorderRequest.objects.filter(status="ordered").order_by(
                        "-ordered_at"
                    ),
                    to_attr="_ordered_reorder_requests",
                ),
            )
            .all()
        )

        # Filter by SIG ownership (list policy: staff/super/Logistics and
        # regular users see everything; SIG admins see only their SIGs' items).
        from membership.services import OwnershipVisibility, scope_queryset_by_ownership

        queryset = scope_queryset_by_ownership(
            queryset, self.request.user, policy=OwnershipVisibility.LIST
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

        # Kit visibility (op-8n0). Kits live on this table but are not stock —
        # they are purchasing constructs whose receipts credit their components
        # — so they would otherwise appear in every item picker, count sheet and
        # low-stock list as a permanently-zero item. Excluded by default,
        # mirroring the ``include_retired`` opt-out directly above.
        #
        # NOTE: this is a CLIENT-VISIBLE CONTRACT CHANGE for
        # ``/api/inventory/items/``. ``?is_kit=true`` returns only kits and
        # ``?include_kits=true`` returns both, so any consumer that wants the
        # old "everything" behaviour has an explicit opt-in.
        is_kit_param = self.request.query_params.get("is_kit", "").lower()
        include_kits = self.request.query_params.get("include_kits", "").lower()
        if is_kit_param == "true":
            queryset = queryset.filter(is_kit=True)
        elif is_kit_param == "false" or include_kits != "true":
            queryset = queryset.filter(is_kit=False)

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

        from membership.services import can_assign_to_owning_group

        # Check ownership_type if provided
        ownership_type = request.data.get("ownership_type")
        owning_group_id = request.data.get("owning_group")

        if ownership_type == InventoryItem.OwnershipType.GROUP and owning_group_id:
            try:
                group = Group.objects.get(pk=owning_group_id)
                if not can_assign_to_owning_group(user, group):
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

        from membership.services import can_assign_to_owning_group, can_manage_sig_inventory

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
                    if not can_assign_to_owning_group(user, group):
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
    def kits(self, request, pk=None):
        """List the kits that supply this component (op-8n0).

        Reorder triage is "show, don't act": a purchaser looking at low cyan ink
        should be able to see that the Eufy Ink Kit is a way to buy it, without
        this endpoint (or anything downstream of it) deciding that for them.

        Returns ``[]`` for an item nothing contains, and for a kit itself —
        nested kits are out of scope, so a kit is never a component.
        """
        item = self.get_object()
        kits = (
            InventoryItem.objects.filter(
                is_kit=True,
                kit_components__component=item,
            )
            .prefetch_related("kit_components", item_suppliers_prefetch())
            .distinct()
            .order_by("name")
        )
        serializer = KitSummarySerializer(
            kits, many=True, context={**self.get_serializer_context(), "component_id": item.pk}
        )
        return Response(serializer.data)

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
        """Log usage/consumption of an item, optionally charging a committee.

        Body:
        - ``quantity`` (int, default 1): units consumed. Base units by default —
          unchanged for every existing caller — or whole packs of the item's
          ``count_level`` when ``at_level`` is true ("used 2 cases").
        - ``at_level`` (bool, optional): read ``quantity`` as a pack count and
          convert it through the item's packaging chain (op-ev14). Rejected for
          an item that is not counted in packs. ``UsageLog.quantity_used`` still
          stores base units, so the wire shape of the log is unchanged; the
          response reports which unit was entered.
        - ``notes`` (str, optional).
        - ``charged_group`` (``auth.Group`` id, optional): when given, the
          committee is charged for the consumption. A snapshot of the item's cost
          is taken and a balanced ``SIG_CHARGE`` journal entry (DR 5100 Committee
          supplies expense / CR 1300 Inventory) is posted to the accounting
          ledger in the SAME transaction as the stock decrement (see
          ``accounting.adapters.post_supply_consumption``).

        Charging a committee additionally requires the caller be staff or an
        admin of the item's owning group. With no ``charged_group`` the endpoint
        behaves exactly as before (unchanged behaviour and permissions). If a
        committee is given but the item has no unit cost on file, the committee is
        recorded but nothing is posted and a ``warning`` is returned.
        """
        from django.contrib.auth.models import Group

        from accounting.adapters import post_supply_consumption

        item = self.get_object()
        try:
            entered_quantity = int(request.data.get("quantity", 1))
        except (TypeError, ValueError):
            return Response(
                {"detail": "quantity must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Base units unless the caller says the number is a pack count. Opt-in
        # rather than inferred from ``count_mode``, because a usage quantity is
        # often machine-derived from a base-unit source.
        try:
            at_level = parse_at_level(request.data.get("at_level"))
            quantity = resolve_base_quantity(item, entered_quantity, at_level=at_level)
        except DjangoValidationError as exc:
            return Response(
                {"detail": exc.messages[0]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        notes = request.data.get("notes", "")

        # Optional committee chargeback. Posting money is gated tighter than
        # plain usage logging, so check the charge permission before touching the
        # (public) log_usage flow, then resolve the committee.
        group = None
        raw_group = request.data.get("charged_group")
        if raw_group not in (None, ""):
            if not _user_can_charge_item(request.user, item):
                return Response(
                    {"detail": "You do not have permission to charge a committee for this item."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            try:
                group = Group.objects.get(pk=raw_group)
            except (Group.DoesNotExist, ValueError, TypeError):
                return Response(
                    {"detail": "charged_group is not a valid group."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        warning = None
        with transaction.atomic():
            # Snapshot cost + actor at consume time. Recorded even when no
            # committee is charged (harmless record-keeping); later price changes
            # must never rewrite this history (mirrors the ledger's snapshotting).
            unit_cost = item.unit_cost  # Optional[Decimal] (primary-supplier derived)
            total_cost = unit_cost * quantity if unit_cost is not None else None
            charged_by = request.user if request.user.is_authenticated else None

            usage_log = UsageLog.objects.create(
                item=item,
                quantity_used=quantity,
                notes=notes,
                charged_group=group,
                unit_cost=unit_cost,
                total_cost=total_cost,
                charged_by=charged_by,
            )

            # Update stock (unchanged: never drive stock below 0 here).
            if item.current_stock >= quantity:
                item.current_stock -= quantity
                item.save()

            # Post the SIG_CHARGE only when a committee is charged AND there is a
            # positive cost to post. No cost on file -> record the committee but
            # post nothing, and surface a warning.
            if group is not None:
                if total_cost is not None and total_cost > 0:
                    txn = post_supply_consumption(
                        committee=group,
                        amount=total_cost,
                        source_ref=f"usage:{usage_log.pk}",
                        item=item,
                        created_by=charged_by,
                    )
                    usage_log.ledger_transaction = txn
                    usage_log.save(update_fields=["ledger_transaction"])
                else:
                    warning = (
                        "committee recorded, but the item has no unit cost — "
                        "nothing posted to the ledger"
                    )

        data = UsageLogSerializer(usage_log).data
        # Echo the unit the entry was read in so a caller that sent a pack count
        # can confirm the conversion (``quantity_used`` above is base units).
        data = {
            **data,
            "entered_quantity": entered_quantity,
            "entered_unit": count_unit(item) if at_level else (item.base_unit or "unit"),
            "on_hand_display": on_hand_display(item),
        }
        if warning:
            data = {**data, "warning": warning}
        return Response(data)

    @action(detail=True, methods=["post"], url_path="pack-container")
    def pack_container(self, request, pk=None):
        """Open a sealed pack, or finish the open one (``open_closed`` items).

        The two container moves an ``open_closed`` item makes, which no other
        stock path expresses (op-ev14). Body: ``transition`` — ``"open"`` or
        ``"finish"`` — plus optional ``notes`` carried onto the usage log.

        * ``open`` — a sealed pack is broken into: ``current_stock`` drops by the
          pack's base units, ``open_container_count`` rises by one, and a
          ``UsageLog`` records the pack. Under this mode the countable stock is
          the *sealed* packs and an open pack's remaining contents are untracked,
          so the base units stop being countable the moment it is opened — which
          is why opening is the half that moves stock and writes usage.
        * ``finish`` — the open pack is empty: ``open_container_count`` drops by
          one and stock does not move (it already did). No usage row: nothing was
          consumed here, and a zero-quantity one would violate
          ``UsageLog.quantity_used``'s minimum.

        Returns ``{transition, current_stock, open_container_count,
        on_hand_display, usage_log}`` — ``usage_log`` is null for ``finish``.
        Requires authentication (the ``AllowAny`` ``log_usage`` path stays the
        public way to record consumption); 400 for a non-``open_closed`` item,
        no sealed pack left to open, or no open pack to finish.
        """
        from .services.pack_transitions import finish_open_pack, open_pack

        item = self.get_object()
        transition = (request.data.get("transition") or "").strip().lower()
        if transition not in ("open", "finish"):
            return Response(
                {"detail": "transition is required; choose 'open' or 'finish'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        notes = request.data.get("notes", "") or ""
        try:
            if transition == "open":
                item, usage_log = open_pack(item, user=request.user, notes=notes)
            else:
                item = finish_open_pack(item, user=request.user)
                usage_log = None
        except DjangoValidationError as exc:
            return Response(
                {"detail": exc.messages[0]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "transition": transition,
                "id": str(item.id),
                "current_stock": item.current_stock,
                "open_container_count": item.open_container_count,
                "on_hand_display": on_hand_display(item),
                "usage_log": UsageLogSerializer(usage_log).data if usage_log else None,
            }
        )

    @action(detail=True, methods=["post"], url_path="cycle-count")
    def cycle_count(self, request, pk=None):
        """Record a physical cycle count for this inventory item.

        Reconciles system on-hand to the counted quantity via the shared
        ``_apply_reconciliation_row`` helper — it writes the StockReconciliation
        audit row, sets ``current_stock`` to the actual count, and auto-creates a
        ReorderRequest when at/below minimum (unless ``skip_reorder``). Afterwards
        the item's ``last_counted_at`` is stamped so the detail views can show
        days-since-last-count.

        ``counted_qty`` is base units unless ``at_level`` says it is a count of
        whole packs of the item's ``count_level`` — "I counted 3 cases"
        (op-ev14). ``open_count`` sets the open-container tally of an
        ``open_closed`` item in the same write (the sealed/open pair). The
        response echoes ``counted_unit`` and ``on_hand_display`` so a caller can
        confirm which unit was applied.

        Body: ``counted_qty`` (required int >= 0), ``reason`` (required, from
        ``StockReconciliation.ReasonCode.choices``), ``skip_reorder`` (optional bool),
        ``notes`` (optional str), ``at_level`` (optional bool), ``open_count``
        (optional int >= 0, ``open_closed`` items only).
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
        valid_reasons = {v for v, _ in StockReconciliation.ReasonCode.choices}
        if reason not in valid_reasons:
            return Response(
                {"detail": ("reason is required; choose from " f"{sorted(valid_reasons)}.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        skip_reorder = bool(request.data.get("skip_reorder", False))
        notes = request.data.get("notes", "") or ""

        try:
            at_level = parse_at_level(request.data.get("at_level"))
        except DjangoValidationError as exc:
            return Response(
                {"detail": exc.messages[0]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw_open_count = request.data.get("open_count")
        open_count = None
        if raw_open_count is not None:
            try:
                open_count = int(raw_open_count)
            except (TypeError, ValueError):
                return Response(
                    {"detail": "open_count must be an integer."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if open_count < 0:
                return Response(
                    {"detail": "open_count must be >= 0."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            with transaction.atomic():
                reconciliation, _reorder_created = _apply_reconciliation_row(
                    request.user,
                    item,
                    counted_qty,
                    reason,
                    notes=notes,
                    skip_reorder=skip_reorder,
                    at_level=at_level,
                    open_count=open_count,
                )
                item.last_counted_at = timezone.now()
                item.save(update_fields=["last_counted_at"])
        except DjangoValidationError as exc:
            return Response(
                {"detail": exc.messages[0]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        days_since_last_count = (timezone.now() - item.last_counted_at).days

        return Response(
            {
                "id": str(item.id),
                "current_stock": item.current_stock,
                "counted_unit": count_unit(item),
                "on_hand_display": on_hand_display(item),
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

    @action(detail=True, methods=["get"], permission_classes=[IsAuthenticated])
    def stock_history(self, request, pk=None):
        """Time series + event markers powering the Stock-History chart (op-izy5).

        Returns the item's weekly stock snapshots plus two event overlays and
        the chart's reference lines:

        * ``series`` — ``[{date, count}]`` from ``StockLevelSnapshot`` (the
          weekly counts written by ``inventory.tasks.snapshot_stock_levels``),
          chronological.
        * ``reorder_events`` — ``[{date}]`` from ``ReorderRequest`` for this
          item: when a reorder was raised.
        * ``cycle_counts`` — ``[{date, count}]`` from ``StockReconciliation``
          (``projected_count`` is the stock at reconciliation time). Carries the
          count so the chart has real historical points immediately, before the
          weekly snapshots accumulate.
        * ``thresholds`` — ``{reorder_point, desired}`` where ``reorder_point``
          is ``minimum_stock`` and ``desired`` is ``minimum_stock +
          reorder_quantity``.
        * ``current_stock`` — the item's live stock level.

        Read-only; no migration beyond the snapshot model.
        """
        item = self.get_object()

        series = [
            {"date": snap.snapshot_date.isoformat(), "count": snap.count}
            for snap in item.stock_snapshots.order_by("snapshot_date")
        ]
        reorder_events = [
            {"date": req.requested_at.date().isoformat()}
            for req in item.reorder_requests.order_by("requested_at")
        ]
        cycle_counts = [
            {"date": rec.reconciled_at.date().isoformat(), "count": rec.projected_count}
            for rec in item.reconciliations.order_by("reconciled_at")
        ]

        return Response(
            {
                "series": series,
                "reorder_events": reorder_events,
                "cycle_counts": cycle_counts,
                "thresholds": {
                    "reorder_point": item.minimum_stock,
                    "desired": item.minimum_stock + item.reorder_quantity,
                },
                "current_stock": item.current_stock,
            }
        )

    @action(detail=True, methods=["get"], permission_classes=[IsAuthenticated])
    def purchase_history(self, request, pk=None):
        """Order + receipt provenance for the item detail view (op-96uo).

        Exposes history that already exists on the PO/delivery models so the
        detail screen can answer "what did we pay per order, on which order,
        and what shipped when":

        * ``order_costs`` — one row per ``PurchaseOrderItem`` for this item,
          oldest order first: ``{purchase_order, po_number, order_date, status,
          quantity_ordered, unit_cost_ordered, unit_cost_actual}``. This is the
          full list behind ``metrics``' ``last_po_unit_cost``, which keeps only
          the newest row (see ``services/item_metrics.py``).
        * ``deliveries`` — one row per ``DeliveryItem``, oldest delivery first:
          ``{purchase_order, po_number, delivery_date, tracking_number,
          carrier, quantity_received, receipt_notes, is_complete}``. A
          partially-shipped order has several deliveries and therefore several
          tracking numbers; all of them are returned.

        Both lists are flat and carry the PO pk so a client can group by order
        even when ``po_number`` is still unassigned (it is nullable). Joining
        through ``item_supplier`` excludes asset-only and freeform PO lines,
        which carry no inventory item. Read-only; no migration.

        Auth-required, like ``stock_history`` and unlike the public
        ``metrics``/``retrieve`` reads, because it surfaces supplier pricing.
        """
        from reorder_queue.models import DeliveryItem, PurchaseOrderItem

        item = self.get_object()

        # Both lists tie-break on ``id``: ``order_date`` defaults to "now" (and
        # backdated orders are often entered in a batch), and partial shipments
        # are often logged together, so rows sharing a timestamp would otherwise
        # come back in an arbitrary order.
        order_lines = (
            PurchaseOrderItem.objects.filter(item_supplier__item_id=item.id)
            .select_related("purchase_order")
            .order_by("purchase_order__order_date", "id")
        )
        order_costs = [
            {
                "purchase_order": line.purchase_order_id,
                "po_number": line.purchase_order.po_number,
                "order_date": line.purchase_order.order_date,
                "status": line.purchase_order.status,
                "quantity_ordered": line.quantity_ordered,
                "unit_cost_ordered": line.unit_cost_ordered,
                "unit_cost_actual": line.unit_cost_actual,
            }
            for line in order_lines
        ]

        delivery_items = (
            DeliveryItem.objects.filter(purchase_order_item__item_supplier__item=item)
            .select_related("delivery", "delivery__purchase_order")
            .order_by("delivery__delivery_date", "id")
        )
        deliveries = [
            {
                "purchase_order": received.delivery.purchase_order_id,
                "po_number": received.delivery.purchase_order.po_number,
                "delivery_date": received.delivery.delivery_date,
                "tracking_number": received.delivery.tracking_number,
                "carrier": received.delivery.carrier,
                "quantity_received": received.quantity_received,
                "receipt_notes": received.delivery.receipt_notes,
                "is_complete": received.delivery.is_complete,
            }
            for received in delivery_items
        ]

        return Response(
            {
                "order_costs": ItemOrderCostSerializer(order_costs, many=True).data,
                "deliveries": ItemDeliverySerializer(deliveries, many=True).data,
            }
        )

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


class KitViewSet(viewsets.ModelViewSet):
    """Kit SKUs: purchasable bundles that decompose into component stock (op-8n0).

    A FACADE over the ``is_kit=True`` slice of :class:`InventoryItem` rather
    than a model of its own, which is why ``basename`` must be set explicitly on
    the router — DRF would otherwise infer ``inventoryitem`` from the queryset
    model and collide with ``/items/``.

    Reads are public and writes require authentication, matching
    ``InventoryItemViewSet``: a kit is catalog data, and the purchasing screens
    that show "this cartridge comes in the Eufy Ink Kit" are the same public
    surfaces that show the cartridge.
    """

    serializer_class = KitSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        """Kits only, with the bill of materials and supplier terms prefetched.

        Because :class:`KitSerializer` inherits the whole
        ``InventoryItemSerializer`` field set, it also inherits that
        serializer's per-row lookups, so this MUST mirror
        ``InventoryItemViewSet.get_queryset``'s joins or the list goes
        quadratic. Measured: ~5 queries per kit without them.

        On top of that shared set, ``kit_components__component`` feeds the
        nested rows and ``component_count``.

        The two ``reorder_requests`` prefetches look pointless for kits — a kit
        is never requestable, so both always come back empty — but the
        serializer's ``reorder_status`` / ``has_pending_reorder`` /
        ``expected_delivery_date`` / ``active_reorder_request`` fields query
        them per row unless the cache exists. Two constant queries beat 4N.
        """
        from django.db.models import Prefetch

        from reorder_queue.models import ReorderRequest

        queryset = (
            InventoryItem.objects.filter(is_kit=True)
            .select_related("category", "location", "safety_profile", "count_level")
            .prefetch_related(
                "kit_components__component",
                item_suppliers_prefetch(),
                "packaging_levels",
                Prefetch(
                    "reorder_requests",
                    queryset=ReorderRequest.objects.filter(
                        status__in=["pending", "approved", "ordered"]
                    ).order_by("-requested_at"),
                    to_attr="_active_reorder_requests",
                ),
                Prefetch(
                    "reorder_requests",
                    queryset=ReorderRequest.objects.filter(status="ordered").order_by(
                        "-ordered_at"
                    ),
                    to_attr="_ordered_reorder_requests",
                ),
            )
        )

        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(sku__icontains=search)
                | Q(description__icontains=search)
                | Q(item_suppliers__supplier_sku__icontains=search)
            ).distinct()

        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == "true")

        supplier = self.request.query_params.get("supplier")
        if supplier:
            queryset = queryset.filter(item_suppliers__supplier_id=supplier).distinct()

        # "Which kits would restock this item?" — the same question
        # ``/items/{id}/kits/`` answers, reachable from the kit list too.
        component = self.request.query_params.get("component")
        if component:
            queryset = queryset.filter(kit_components__component_id=component).distinct()

        ordering = self.request.query_params.get("ordering", "name")
        valid_ordering_fields = {"name", "-name", "sku", "-sku", "created_at", "-created_at"}
        if ordering not in valid_ordering_fields:
            ordering = "name"
        return queryset.order_by(ordering)


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
            change_type=PriceHistory.ChangeType.UPDATED,  # Only actual price updates, not initial records
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

        # Filter by SIG ownership (list policy: staff/super/Logistics and
        # regular users see all assets — including space-owned; SIG admins see
        # only assets owned by their SIGs).
        from membership.services import OwnershipVisibility, scope_queryset_by_ownership

        queryset = scope_queryset_by_ownership(
            queryset, self.request.user, policy=OwnershipVisibility.LIST
        )

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

        # Filter to assets that use a given inventory item as a consumable/part
        # (AssetPart through-model). Drives the serialized-component install
        # picker: a serialized unit installs only into assets its item is a
        # part for (e.g. an ink cartridge → only its printers) (op-sk0s). Ignore
        # a blank/non-UUID value gracefully rather than 500 on a bad filter.
        consumable_for_item = self.request.query_params.get("consumable_for_item")
        if consumable_for_item:
            try:
                uuid.UUID(str(consumable_for_item))
            except (ValueError, TypeError):
                pass  # Not a valid item id — ignore the filter.
            else:
                queryset = queryset.filter(asset_parts__part_id=consumable_for_item).distinct()

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

        # Check ownership_type if provided
        ownership_type = request.data.get("ownership_type")
        owning_group_id = request.data.get("owning_group")

        if ownership_type == Asset.OwnershipType.GROUP and owning_group_id:
            # Check if user is admin of the specified group
            from django.contrib.auth.models import Group

            from membership.services import can_assign_to_owning_group

            try:
                group = Group.objects.get(pk=owning_group_id)
                if not can_assign_to_owning_group(user, group):
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

        from membership.services import can_manage_sig_asset

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

                from membership.services import can_assign_to_owning_group

                try:
                    group = Group.objects.get(pk=owning_group_id)
                    if not can_assign_to_owning_group(user, group):
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

    @action(detail=False, methods=["post"], permission_classes=[IsAdminUser])
    def set_cost_recoverable_by_category(self, request):
        """Bulk-set ``is_cost_recoverable`` on every asset in one category.

        The REST twin of the ``mark_cost_recoverable`` / ``unmark_cost_recoverable``
        admin actions (``inventory/admin.py``): flagging the landlord-billable
        assets one PATCH at a time is unusable for a whole category (all the
        HVAC equipment, say), so this does it in a single ``UPDATE``.

        Body:
        * ``category`` — Category PK (int) **or** slug. Required.
        * ``is_cost_recoverable`` — bool, default ``True``. ``False`` is the undo.

        Matching mirrors the cost-recovery statement's own category expansion
        (``category_id__in``): exact category, child categories are not pulled
        in. Only rows that actually change are written, so ``updated`` is the
        count of assets whose flag flipped — never the size of the category.
        """
        raw_category = request.data.get("category")
        if raw_category is None or (isinstance(raw_category, str) and not raw_category.strip()):
            return Response(
                {"detail": "category is required (Category id or slug)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw_value = request.data.get("is_cost_recoverable", True)
        if isinstance(raw_value, str):
            normalized = raw_value.strip().lower()
            if normalized in {"true", "1"}:
                value = True
            elif normalized in {"false", "0"}:
                value = False
            else:
                return Response(
                    {"detail": "is_cost_recoverable must be a boolean."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        elif isinstance(raw_value, bool):
            value = raw_value
        else:
            return Response(
                {"detail": "is_cost_recoverable must be a boolean."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        lookup = str(raw_category).strip()
        category = Category.objects.filter(slug=lookup).first()
        if category is None and lookup.isdigit():
            category = Category.objects.filter(pk=int(lookup)).first()
        if category is None:
            return error_response(
                ErrorCode.NOT_FOUND,
                f"Category not found: {lookup!r}.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        # ``exclude`` the already-correct rows so the count reports real changes
        # (same semantics as the admin action's ``filter(is_cost_recoverable=…)``).
        matched = Asset.objects.filter(category_id=category.pk)
        updated = matched.exclude(is_cost_recoverable=value).update(is_cost_recoverable=value)

        return Response(
            {
                "category_id": category.pk,
                "category_slug": category.slug,
                "category_name": category.name,
                "is_cost_recoverable": value,
                "matched": matched.count(),
                "updated": updated,
            }
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
        if asset.status in [asset.Status.IMPLEMENTING, asset.Status.TESTING]:
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
        if asset.status in [asset.Status.IMPLEMENTING, asset.Status.TESTING]:
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
        new_status = request.data.get("status", AssetProblem.Status.RESOLVED)
        if new_status not in [AssetProblem.Status.RESOLVED, AssetProblem.Status.CLOSED]:
            return Response(
                {
                    "error": f"Invalid status. Must be '{AssetProblem.Status.RESOLVED}' or '{AssetProblem.Status.CLOSED}'"
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        problem.status = new_status
        problem.resolution_notes = request.data.get(
            "resolution_notes", problem.resolution_notes or ""
        )

        # Set resolved_at and resolved_by if resolving for the first time
        if (
            new_status in [AssetProblem.Status.RESOLVED, AssetProblem.Status.CLOSED]
            and not problem.resolved_at
        ):
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
    exposes detail GET, the upload-photo action so reporters can attach images
    to a freshly-created problem report, and the promote/resolve actions that
    turn a report into real work and close it out.
    """

    queryset = AssetProblem.objects.select_related(
        "asset", "part", "work_order", "third_party_work_order"
    ).prefetch_related("photos")
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

    @action(
        detail=True,
        methods=["post"],
        url_path="promote-standard",
        permission_classes=[IsAuthenticated],
    )
    def promote_to_standard_work_order(self, request, pk=None):
        """Promote this AssetProblem to an in-house corrective WorkOrder.

        No MaintenanceItem picker, unlike the LocationProblem sibling: a
        corrective work order anchors directly to the problem's asset
        (``maintenance_item=None``), which is exactly what the nullable-item
        foundation exists for. The reporter's description becomes the work
        order's notes and their photos are copied onto it.
        """
        from .services.problem_promotion import copy_to_work_order_photo
        from .services.work_order_loto import create_loto_completions

        problem = self.get_object()
        if problem.work_order_id:
            return Response(
                {"error": "Already promoted to a standard work order."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = request.user if request.user.is_authenticated else None
        with transaction.atomic():
            wo = WorkOrder.objects.create(
                maintenance_item=None,
                asset=problem.asset,
                notes=problem.description,
                assigned_to=user,
            )
            # Materialize the per-energy-source LOTO rows so a corrective WO
            # prints and scans back exactly like a generated PM one.
            create_loto_completions(wo)
            problem.work_order = wo
            problem.status = AssetProblem.Status.IN_PROGRESS
            problem.save(update_fields=["work_order", "status", "updated_at"])
            for photo in problem.photos.all():
                if not photo.image:
                    continue
                copy_to_work_order_photo(
                    photo.image,
                    wo,
                    caption=photo.caption or f"From AssetProblem {problem.id}",
                    filename_hint=f"asset-problem-{problem.id}",
                )

        serializer = AssetProblemSerializer(problem, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=["post"],
        url_path="promote-third-party",
        permission_classes=[IsAuthenticated],
    )
    def promote_to_third_party_work_order(self, request, pk=None):
        """Promote this AssetProblem to a vendor ThirdPartyWorkOrder.

        Required: ``vendor`` (uuid) and ``title`` — a vendor WO is a purchase,
        so it needs a human-written scope line rather than the raw report text.
        The description rides along in ``notes``, the asset (and its location)
        are pre-filled, and the reporter's photos are copied to attachments.
        """
        from maintenance_orders.models import ThirdPartyWorkOrder, ThirdPartyWorkOrderAttachment
        from vendors.models import Vendor

        from .services.problem_promotion import copy_to_tpwo_attachment

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
        except (Vendor.DoesNotExist, DjangoValidationError, ValueError):
            # DjangoValidationError covers a malformed uuid, which would
            # otherwise 500 out of the queryset rather than 404.
            return Response(
                {"error": "Vendor not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        work_type = request.data.get("work_type") or ThirdPartyWorkOrder.WORK_TYPE_STANDARD
        user = request.user if request.user.is_authenticated else None

        with transaction.atomic():
            tpwo = ThirdPartyWorkOrder.objects.create(
                title=title,
                asset=problem.asset,
                location=problem.asset.location,
                vendor=vendor,
                work_type=work_type,
                notes=problem.description,
                opened_by=user,
            )
            problem.third_party_work_order = tpwo
            problem.status = AssetProblem.Status.IN_PROGRESS
            problem.save(update_fields=["third_party_work_order", "status", "updated_at"])
            for photo in problem.photos.all():
                if not photo.image:
                    continue
                copy_to_tpwo_attachment(
                    photo.image,
                    tpwo,
                    kind=ThirdPartyWorkOrderAttachment.KIND_PHOTO,
                    caption=photo.caption or f"Reporter photo from AssetProblem {problem.id}",
                    filename_hint="asset-problem-photo",
                    user=user,
                )

        serializer = AssetProblemSerializer(problem, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def resolve(self, request, pk=None):
        """Mark this asset problem resolved or closed.

        Same stamp as ``AssetViewSet.resolve_problem``, reachable from the
        problem itself so web and ScanTTY don't need the asset in hand.
        """
        problem = self.get_object()
        new_status = request.data.get("status", AssetProblem.Status.RESOLVED)
        if new_status not in (AssetProblem.Status.RESOLVED, AssetProblem.Status.CLOSED):
            return Response(
                {
                    "error": (
                        f"status must be '{AssetProblem.Status.RESOLVED}' or "
                        f"'{AssetProblem.Status.CLOSED}'"
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
            problem.resolved_by = actor_display(request.user)
        problem.save()

        serializer = AssetProblemSerializer(problem, context={"request": request})
        return Response(serializer.data)


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
            source=AssetMeterReading.Source.MANUAL,
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
            source=AssetMeterReading.Source.MANUAL_ADJUST,
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
                asset=mi.asset,
                notes=problem.description,
                assigned_to=request.user if request.user.is_authenticated else None,
            )
            problem.work_order = wo
            problem.status = LocationProblem.Status.IN_PROGRESS
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
            problem.status = LocationProblem.Status.IN_PROGRESS
            problem.save(update_fields=["third_party_work_order", "status", "updated_at"])

        serializer = LocationProblemSerializer(problem, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def resolve(self, request, pk=None):
        """Mark this location problem as resolved or closed."""
        problem = self.get_object()
        new_status = request.data.get("status", LocationProblem.Status.RESOLVED)
        if new_status not in (LocationProblem.Status.RESOLVED, LocationProblem.Status.CLOSED):
            return Response(
                {
                    "error": (
                        f"status must be '{LocationProblem.Status.RESOLVED}' or "
                        f"'{LocationProblem.Status.CLOSED}'"
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
            action=MaintenanceAuditEvent.Action.LOCATION_PROBLEM_RESOLVE,
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
        from .services.problem_promotion import copy_to_work_order_photo

        if problem.photo:
            copy_to_work_order_photo(
                problem.photo,
                work_order,
                caption=f"From LocationProblem {problem.id}",
                filename_hint=f"location-problem-{problem.id}",
            )

    @staticmethod
    def _copy_to_tpwo_attachment(file_field, tpwo, *, kind, caption, filename_hint, user):
        """Copy one of a problem's files to a TPWO attachment."""
        from .services.problem_promotion import copy_to_tpwo_attachment

        copy_to_tpwo_attachment(
            file_field,
            tpwo,
            kind=kind,
            caption=caption,
            filename_hint=filename_hint,
            user=user,
        )


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

        if self.action == "retrieve":
            # FixtureDetailSerializer.recent_refill_requests reads each request's
            # actor FKs (#888) — prefetch them so the detail view stays flat.
            queryset = queryset.prefetch_related(
                "refill_requests__requested_user",
                "refill_requests__resolved_user",
            )
        elif self.action == "list":
            # FixtureSerializer.pending_requests_count would otherwise fire a
            # per-row PENDING .count() (the fixture-list N+1, issue #890).
            # Prefetch the PENDING subset into a to_attr the property reads via
            # len(); a same-named annotate would be shadowed by the property.
            # One query regardless of page size.
            from django.db.models import Prefetch

            queryset = queryset.prefetch_related(
                Prefetch(
                    "refill_requests",
                    queryset=FixtureRefillRequest.objects.filter(
                        status=FixtureRefillRequest.Status.PENDING
                    ),
                    to_attr="_pending_refill_requests",
                )
            )

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

        # Actor identity (#888): link the auth user when signed in, else leave
        # the FK null for an anonymous kiosk scan (this endpoint stays AllowAny).
        # The paired requested_by string holds the display name (handle/username)
        # or stays blank for an anonymous scan — unchanged behaviour.
        requested_user = None
        requested_by = ""
        if request.user and request.user.is_authenticated:
            requested_user = request.user
            requested_by = actor_display(request.user)

        # Create the refill request
        notes = request.data.get("notes", "")
        refill_request = FixtureRefillRequest.objects.create(
            fixture=fixture,
            requested_user=requested_user,
            requested_by=requested_by,
            notes=notes,
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

        # Actor identity (#888): record the resolver's auth link + display name.
        resolved_user = request.user if request.user.is_authenticated else None
        resolved_by = actor_display(request.user) if request.user.is_authenticated else ""
        notes = request.data.get("notes", "")

        # Update all pending requests
        from django.utils import timezone

        updated_count = FixtureRefillRequest.objects.filter(
            fixture=fixture, status=FixtureRefillRequest.Status.PENDING
        ).update(
            status=FixtureRefillRequest.Status.COMPLETED,
            resolved_at=timezone.now(),
            resolved_user=resolved_user,
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
        "fixture",
        "fixture__location",
        "fixture__refill_item",
        # Actor FKs (#888) are read by the serializer's *_actor / *_username
        # fields — join them to keep the list endpoint free of per-row lookups.
        "requested_user",
        "resolved_user",
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

    def perform_create(self, serializer):
        """Stamp the requesting actor on create (#888).

        Signed-in users get the FK + their handle/username display name; the
        create endpoint stays AllowAny, so an anonymous kiosk caller keeps a
        null FK and whatever display name they supplied in the request body.
        requested_by is read-only on the serializer now, so the anon name is
        read straight from request.data here — unchanged behaviour for the
        legacy string.
        """
        user = self.request.user
        if user and user.is_authenticated:
            serializer.save(requested_user=user, requested_by=actor_display(user))
        else:
            serializer.save(
                requested_user=None,
                requested_by=self.request.data.get("requested_by", ""),
            )

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        """Mark a single refill request as completed."""
        refill_request = self.get_object()

        if refill_request.status == FixtureRefillRequest.Status.COMPLETED:
            return Response(
                {"error": "This request is already completed"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from django.utils import timezone

        refill_request.status = FixtureRefillRequest.Status.COMPLETED
        refill_request.resolved_at = timezone.now()
        # Actor identity (#888): auth link + display name for the resolver.
        if request.user.is_authenticated:
            refill_request.resolved_user = request.user
            refill_request.resolved_by = actor_display(request.user)
        else:
            refill_request.resolved_user = None
            refill_request.resolved_by = ""
        refill_request.notes = request.data.get("notes", refill_request.notes)
        refill_request.save()

        serializer = self.get_serializer(refill_request)
        return Response(serializer.data)


class InventoryReportViewSet(viewsets.ViewSet):
    """API endpoint for inventory reports."""

    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["get"])
    def stock_by_category(self, request):
        """Stock levels aggregated by category.

        ``total_value`` is the value of the stock this report CAN price — items
        no active supplier quotes a price for contribute nothing to it — and
        ``items_without_price`` is how many of them there were, so the number
        is read as the lower bound it has always been rather than as a
        complete valuation (op-9m2v).
        """
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
                # How many items the total above could NOT value, because no
                # active supplier records a price for them (op-9m2v). The
                # ``Coalesce(..., 0)`` is the SQL twin of ``unit_cost or 0``:
                # an unpriced item contributes nothing and the total reads as
                # complete. The number is deliberately UNCHANGED — moving it
                # would be inventing money — and the count beside it is what
                # makes the claim honest.
                items_without_price=Count("id", filter=Q(unit_cost_value__isnull=True)),
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
                    "items_without_price": item["items_without_price"],
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
        """Total inventory value grouped by location.

        ``total_value`` / ``items_without_price``: see ``stock_by_category``.
        The number is the value of the stock this report CAN price and is
        deliberately unchanged; the count beside it is what stops it reading as
        a complete valuation (op-9m2v).
        """
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
                # See ``stock_by_category`` — same SQL twin, same honesty
                # count, same deliberately-unchanged total.
                items_without_price=Count("id", filter=Q(unit_cost_value__isnull=True)),
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
                    "items_without_price": item["items_without_price"],
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
    def demand_forecast(self, request):
        """Restock-interval demand-forecast report for non-serialized items.

        Returns the latest stored :class:`~inventory.models.DemandForecast` row
        per active, non-retired, non-serialized item that has a forecast,
        most-urgent first (reorder-flagged, then soonest due). Reads *stored*
        rows only -- until the nightly forecasting task populates the table this
        returns ``[]``.

        Query params:
            ``low_stock_only`` -- when truthy, only items whose
            ``needs_reorder`` flag is set (due within their lead time) are
            returned.
        """
        from inventory.serializers import DemandForecastSerializer
        from inventory.services.demand_forecast import latest_demand_forecasts

        low_stock_only = str(request.query_params.get("low_stock_only", "")).lower() in (
            "1",
            "true",
            "yes",
        )

        forecasts = latest_demand_forecasts(low_stock_only=low_stock_only)
        serializer = DemandForecastSerializer(forecasts, many=True, context={"request": request})
        return Response(serializer.data)

    @action(detail=False, methods=["get"])
    def reorder_alerts(self, request):
        """Predictive reorder-alert notify set.

        Returns the latest forecast row for every item that opted in
        (``reorder_alerts_enabled=True``) AND is due to reorder
        (``needs_reorder=True``), most-urgent first. Reads *stored* rows only --
        returns ``[]`` until the forecasting task has run.
        """
        from inventory.serializers import DemandForecastSerializer
        from inventory.services.demand_forecast import reorder_alert_forecasts

        forecasts = reorder_alert_forecasts()
        serializer = DemandForecastSerializer(forecasts, many=True, context={"request": request})
        return Response(serializer.data)

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
                    # The count that qualifies the total beside it, carried on
                    # the CSV as well as on the JSON payload, the UI table and
                    # the browser-side export in ``csvExport.ts`` (op-9m2v).
                    # ``total_value`` is ``SUM(stock * COALESCE(unit_cost, 0))``
                    # and reads as a complete valuation; a spreadsheet is the
                    # surface most likely to sum it.
                    "items_without_price",
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
                        "items_without_price": row["items_without_price"],
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
                fieldnames=[
                    "location_name",
                    "total_items",
                    "total_stock",
                    "total_value",
                    # See ``stock_by_category`` above — same partial total,
                    # same honesty count.
                    "items_without_price",
                ],
            )
            writer.writeheader()
            for row in data:
                writer.writerow(
                    {
                        "location_name": row["location_name"],
                        "total_items": row["total_items"],
                        "total_stock": row["total_stock"],
                        "total_value": f"{row['total_value']:.2f}",
                        "items_without_price": row["items_without_price"],
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


class MaintenanceToolViewSet(viewsets.ModelViewSet):
    """API endpoint for maintenance tools (what to grab before starting)."""

    queryset = MaintenanceTool.objects.select_related(
        "maintenance_item__asset", "inventory_item"
    ).all()
    serializer_class = MaintenanceToolSerializer
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
    """API endpoint for maintenance task steps (line items within a MaintenanceItem).

    Multipart is enabled because a step carries a ``reference_image`` (the
    instructional photo the template editor uploads per step); JSON still works
    for the image-less fields.
    """

    queryset = MaintenanceTask.objects.select_related("maintenance_item__asset").all()
    serializer_class = MaintenanceTaskSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

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
            # The work order's own asset — the one every serializer field and
            # context builder reads, and the only one a corrective WO has.
            "asset__location",
            "asset__manufacturer",
            "asset__category",
            "assigned_to",
        )
        .prefetch_related(
            "task_completions__completed_by",
            "task_completions__task",
            # op-syov: the per-step evidence gallery. Without this the WO detail
            # serializer runs one photo query per step (plus one per uploader).
            "task_completions__evidence_photos__uploaded_by",
            # op-768w: ``stock_item`` reads the ad-hoc line's direct link OR
            # the template line's spec link — prefetch both halves so
            # serialising ``inventory_item_name`` across a WO's materials
            # costs nothing extra.
            "material_usage__material__inventory_item",
            "material_usage__inventory_item",
            "loto_completions__completed_by",
            "loto_completions__energy_source",
            "photos__uploaded_by",
            "maintenance_item__materials",
            # op-67q5: feed WorkOrderSerializer.get_tools (the up-front "Tools
            # Required" list) from one prefetch instead of a query per row.
            "maintenance_item__tools",
            # op-0v4: the work order's own tool rows — the branch get_tools
            # prefers — plus the location each row falls back to when it has no
            # per-job hint. Without the location join, resolving a tool's
            # location costs a query per row.
            "tools__inventory_item__location",
            # op-o6rs: feed the pending-review badge (pending_review_count) from
            # a single prefetch instead of a per-row submissions query (N+1).
            "submissions",
            # op-pzae: feed WorkOrderSerializer.reference_documents. One query
            # for the asset's whole document library — the revision chains are
            # then walked in Python off this cache, so a deep supersedes chain
            # costs nothing extra.
            "maintenance_item__asset__documents",
            "asset__documents",
            # op-bu80: feed WorkOrderSerializer.purchase_order_lines ("ordered
            # for this WO"). One query for the lines plus their PO header/
            # supplier, instead of a query per line per work order.
            "purchase_order_items__purchase_order__supplier",
            "purchase_order_items__item_supplier__item",
            "purchase_order_items__asset",
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
            # Both sides of the union: a bare ``maintenance_item__asset_id=``
            # is an INNER JOIN through the (now nullable) template, which would
            # silently drop every corrective work order on this asset.
            queryset = queryset.filter(Q(maintenance_item__asset_id=asset) | Q(asset_id=asset))
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
            new_status == WorkOrder.Status.COMPLETED
            and instance.status != WorkOrder.Status.COMPLETED
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
        if work_order.status == WorkOrder.Status.COMPLETED:
            if work_order.completed_at is None:
                work_order.completed_at = timezone.now()
                work_order.save(update_fields=["completed_at"])
        elif was_completed and work_order.completed_at is not None:
            work_order.completed_at = None
            work_order.save(update_fields=["completed_at"])

    @staticmethod
    def _finalize_timers(work_order) -> None:
        """Stop the stopwatch(es) when a work order closes.

        Runs between the completed_at stamp and the MaintenanceLog write so the
        accumulated total is final before ``_sync_maintenance_item_completion``
        reads it. A non-completed save is a no-op — pausing on every PATCH would
        stop the clock every time a tech edits a note.
        """
        if work_order.status != WorkOrder.Status.COMPLETED:
            return
        from .services.work_order_timer import finalize_work_order_timers

        finalize_work_order_timers(work_order)

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

        op-m3so: the log also picks up ``time_spent_minutes`` from the work
        order's stopwatch — see ``apply_elapsed_to_log`` for the precedence
        (anything already on the log wins).
        """
        from .services.work_order_timer import apply_elapsed_to_log

        if work_order.status != WorkOrder.Status.COMPLETED:
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
            log = MaintenanceLog.objects.filter(
                work_order=work_order, maintenance_item_id=item_id
            ).first()
            if log is None:
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

            # The stopwatch measures the whole visit, so it cannot be split
            # across bundled siblings — it lands on the PRIMARY item's log only.
            # Writing the same minutes to every bundled log would double-count
            # them the moment anyone sums time spent per asset.
            if item_id == work_order.maintenance_item_id:
                apply_elapsed_to_log(log, work_order)

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
        # Materialize per-energy-source LOTO completion rows so any created WO
        # (not just the generate_work_order path) can be printed + scanned back.
        from .services.work_order_loto import create_loto_completions

        create_loto_completions(work_order)
        self._sync_completion_timestamp(work_order, was_completed=False)
        # Roll same-asset PMs due within PM_AUTO_BUNDLE_DUE_WITHIN_DAYS
        # BEFORE the completion cascade, so a new WO that lands in
        # status=completed (rare, but possible via paper-form ingest)
        # also closes every bundled sibling.
        self._bundle_due_siblings(work_order)
        self._finalize_timers(work_order)
        self._sync_maintenance_item_completion(work_order, actor=self.request.user)
        record_maintenance_audit_event(
            action=MaintenanceAuditEvent.Action.WO_CREATE,
            actor=self.request.user,
            work_order=work_order,
            metadata={
                "maintenance_item_id": (
                    str(work_order.maintenance_item_id) if work_order.maintenance_item_id else None
                ),
                "asset_id": str(work_order.asset_id) if work_order.asset_id else None,
                "due_date": work_order.due_date.isoformat() if work_order.due_date else None,
            },
        )

    def _sync_committee_ledger_charge(self, work_order, *, old_status) -> None:
        """Keep the committee's ledger in step with this work order's status.

        Completing a job on a committee-owned asset charges that committee for
        what it consumed (DR 5100 / CR 1300, keyed ``wo_complete:<id>``);
        reopening a charged job posts the append-only reversal that gives the
        money back. Everything about *which* committee, *how much*, and how the
        charge/reversal cycle stays idempotent lives in the service — see
        :mod:`inventory.services.work_order_ledger`.

        The ledger warning (committee-owned job, nothing priced to charge) is
        stashed for :meth:`_with_ledger_warning` to surface on the response, the
        way ``log_usage`` reports the same situation.
        """
        from .services.work_order_ledger import (
            charge_completed_work_order,
            reverse_work_order_charge,
        )

        became_completed = (
            work_order.status == WorkOrder.Status.COMPLETED
            and old_status != WorkOrder.Status.COMPLETED
        )
        left_completed = (
            old_status == WorkOrder.Status.COMPLETED
            and work_order.status != WorkOrder.Status.COMPLETED
        )
        if became_completed:
            _txn, self._ledger_warning = charge_completed_work_order(
                work_order, actor=self.request.user
            )
        elif left_completed:
            reverse_work_order_charge(work_order, actor=self.request.user)

    def _with_ledger_warning(self, response):
        """Attach the pending committee-ledger warning, if this save raised one."""
        warning = getattr(self, "_ledger_warning", None)
        if warning and isinstance(response.data, dict):
            response.data = {**response.data, "warning": warning}
        return response

    def perform_update(self, serializer):
        old_status = serializer.instance.status
        work_order = serializer.save()
        self._sync_completion_timestamp(
            work_order, was_completed=(old_status == WorkOrder.Status.COMPLETED)
        )
        self._finalize_timers(work_order)
        self._sync_maintenance_item_completion(work_order, actor=self.request.user)
        # Runs on both edges of the completed boundary (charge in, reverse out),
        # so it sits outside the "just completed" block below.
        self._sync_committee_ledger_charge(work_order, old_status=old_status)
        if (
            work_order.status == WorkOrder.Status.COMPLETED
            and old_status != WorkOrder.Status.COMPLETED
        ):
            # A problem promoted to this work order is tracked by it — finishing
            # the work is what resolves the report.
            resolve_problems_for_work_order(
                work_order,
                actor=self.request.user,
                notes=work_order.notes or "",
            )
            record_maintenance_audit_event(
                action=MaintenanceAuditEvent.Action.WO_COMPLETE,
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
        return self._with_ledger_warning(super().update(request, *args, **kwargs))

    def partial_update(self, request, *args, **kwargs):
        gate = self._check_completion_gate(request)
        if gate is not None:
            return gate
        return self._with_ledger_warning(super().partial_update(request, *args, **kwargs))

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
        """Upload a photo to this work order.

        Pass an optional ``task_completion`` (a step id on *this* work order) to
        pin the photo to that step as evidence — "here is what I did". Omitted
        (or blank), the photo stays work-order-level, which is the behaviour
        every existing caller gets.
        """
        work_order = self.get_object()

        task_completion = None
        raw_tc = request.data.get("task_completion")
        # Multipart posts a blank string for an unset field; treat that as absent.
        if raw_tc not in (None, "", "null"):
            try:
                task_completion = work_order.task_completions.get(id=raw_tc)
            except (WorkOrderTaskCompletion.DoesNotExist, DjangoValidationError, ValueError):
                # Belongs to another work order, or is not a step id at all —
                # either way it is not this WO's to pin a photo to.
                return Response(
                    {"task_completion": ["No such task step on this work order."]},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        serializer = WorkOrderPhotoSerializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save(
            work_order=work_order,
            uploaded_by=request.user,
            task_completion=task_completion,
        )
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
        # Ticking a step off means you stopped doing it — the step's stopwatch
        # follows the checkbox so nobody has to remember two taps. Reopening a
        # step deliberately does NOT resume it; that's an explicit start.
        if tc.is_completed:
            tc.pause_timer()
        tc.save()

        # Update work order status to in_progress if any task is completed
        if tc.is_completed and work_order.status == WorkOrder.Status.OPEN:
            work_order.status = WorkOrder.Status.IN_PROGRESS
            work_order.save(update_fields=["status", "updated_at"])

        return Response(WorkOrderTaskCompletionSerializer(tc).data)

    @action(detail=True, methods=["post"], url_path="timer")
    def timer(self, request, pk=None):
        """Start or pause this work order's stopwatch.

        Body: ``{"action": "start" | "pause"}``. Idempotent — starting a running
        clock (or pausing a stopped one) returns 200 with ``changed: false`` and
        touches nothing, so a double-tap or a retry can't corrupt the total.

        WO elapsed is wall-time-on-job: it covers setup, LOTO and cleanup, not
        just the sum of the steps. Starting it also stamps ``started_at`` the
        first time, which is never moved by a later resume.
        """
        from .services.work_order_timer import apply_timer_action

        work_order = self.get_object()
        try:
            changed = apply_timer_action(work_order, request.data.get("action"))
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        data = WorkOrderSerializer(work_order, context={"request": request}).data
        return Response({**data, "changed": changed})

    @action(detail=True, methods=["post"], url_path="tasks/(?P<task_id>[^/.]+)/timer")
    def task_timer(self, request, pk=None, task_id=None):
        """Start or pause the stopwatch on one step of this work order.

        Body: ``{"action": "start" | "pause"}``, same idempotency as
        :meth:`timer`. Only one step per work order runs at a time — starting
        this one pauses whichever other step was running, so the per-step totals
        partition the work instead of overlapping.
        """
        from .services.work_order_timer import apply_timer_action

        work_order = self.get_object()
        try:
            tc = work_order.task_completions.get(id=task_id)
        except WorkOrderTaskCompletion.DoesNotExist:
            return Response(
                {"detail": "Task completion record not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            changed = apply_timer_action(tc, request.data.get("action"))
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        # Doing the work is what moves a WO off OPEN, mirroring complete_task.
        if changed and work_order.status == WorkOrder.Status.OPEN:
            work_order.status = WorkOrder.Status.IN_PROGRESS
            work_order.save(update_fields=["status", "updated_at"])

        data = WorkOrderTaskCompletionSerializer(tc, context={"request": request}).data
        return Response({**data, "changed": changed})

    @action(detail=True, methods=["patch"], url_path="loto/(?P<loto_id>[^/.]+)/complete")
    def complete_loto(self, request, pk=None, loto_id=None):
        """Toggle lockout/tagout of one energy source within this work order.

        Structured safety data only: like task completions, this records that an
        energy source was isolated — it never closes the work order (completion
        stays a required-tasks gate + human confirm).
        """
        work_order = self.get_object()
        try:
            lc = work_order.loto_completions.get(id=loto_id)
        except WorkOrderLotoCompletion.DoesNotExist:
            return Response(
                {"detail": "LOTO completion record not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        is_completed = request.data.get("is_completed")
        if is_completed is None:
            return Response(
                {"detail": "is_completed is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        lc.is_completed = bool(is_completed)
        if lc.is_completed and not lc.completed_at:
            lc.completed_at = timezone.now()
            lc.completed_by = request.user
        elif not lc.is_completed:
            lc.completed_at = None
            lc.completed_by = None
        if "notes" in request.data:
            lc.notes = request.data["notes"]
        lc.save()

        # Locking out a source is part of doing the work — mirror task toggling
        # and advance OPEN → IN_PROGRESS (but never to COMPLETED).
        if lc.is_completed and work_order.status == WorkOrder.Status.OPEN:
            work_order.status = WorkOrder.Status.IN_PROGRESS
            work_order.save(update_fields=["status", "updated_at"])

        return Response(WorkOrderLotoCompletionSerializer(lc).data)

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
                WorkOrderSubmission.Source.SCAN if is_scan else WorkOrderSubmission.Source.MANUAL
            ),
            submitted_by=user,
            from_email=(user.email or "")[:254],
            subject=(upload_name)[:500],
            status=WorkOrderSubmission.Status.RECEIVED,
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
                omr_apply_mark(
                    work_order, change.get("target_id") or "", marked=True, actor=request.user
                )
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
            WorkOrderSubmission.Status.APPLIED
            if not remaining
            else WorkOrderSubmission.Status.PENDING_REVIEW
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
                omr_apply_mark(
                    work_order, change.get("target_id") or "", marked=False, actor=request.user
                )
            dropped += 1

        submission.pending_changes = remaining
        if not remaining and submission.status == WorkOrderSubmission.Status.PENDING_REVIEW:
            submission.status = WorkOrderSubmission.Status.APPLIED
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

    @action(
        detail=True,
        methods=["get"],
        url_path="submissions/(?P<submission_id>[^/.]+)/scan-image",
        permission_classes=[IsAuthenticated],
    )
    def scan_image(self, request, pk=None, submission_id=None):
        """Serve the full scanned page as a PNG for paper-form verification.

        op-o6rs: the per-mark ``mark_crop`` shows only a 72×44 warp of one box;
        to verify the marks against the actual paper the reviewer needs to see
        the whole page. Pulls the embedded page image out of the submission's
        raw scan (falling back to the WO's ``completed_scan``) — no PDF
        rasteriser in the venv, so we read the embedded image the same way
        ``mark_crop`` does — and normalises it to PNG for a stable content-type.
        Authenticated read-only: rendering the page can never mutate state.
        """
        from PIL import Image

        from .services.work_order_ingest import _omr_scan_inputs

        work_order = self.get_object()
        try:
            submission = work_order.submissions.get(id=submission_id)
        except WorkOrderSubmission.DoesNotExist:
            return Response(
                {"detail": "Submission not found for this work order."},
                status=status.HTTP_404_NOT_FOUND,
            )

        source = submission.attachment or work_order.completed_scan
        if not source:
            return Response(
                {"detail": "No scanned page available for this submission."},
                status=status.HTTP_404_NOT_FOUND,
            )

        source.open("rb")
        try:
            raw_bytes = source.read()
        finally:
            source.close()

        _wo_id, _err, image_bytes = _omr_scan_inputs(raw_bytes)
        if image_bytes is None:
            return Response(
                {"detail": "No scanned page image available for this submission."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            with Image.open(io.BytesIO(image_bytes)) as img:
                buf = io.BytesIO()
                img.convert("RGB").save(buf, format="PNG")
            png = buf.getvalue()
        except Exception:  # noqa: BLE001 - unreadable/odd embedded image
            return Response(
                {"detail": "Could not render the scanned page."},
                status=status.HTTP_404_NOT_FOUND,
            )

        response = HttpResponse(png, content_type="image/png")
        response["Content-Disposition"] = f'inline; filename="wo-scan-{submission_id}.png"'
        return response

    @action(detail=True, methods=["patch"], url_path="materials/(?P<material_id>[^/.]+)/toggle")
    def toggle_material(self, request, pk=None, material_id=None):
        """Toggle whether a material was used, syncing inventory stock.

        Marking a material used (``was_used`` false → true) decrements the
        linked inventory item's stock by ``quantity_used`` and writes a
        UsageLog row; un-marking it reverses both. Idempotent and reversible —
        see :func:`inventory.services.work_order_material_usage.apply_material_usage`.

        Body: ``was_used`` (required bool) plus an optional ``quantity_used``
        (the consumed amount) and ``unit_cost`` (the real price paid per unit,
        op-768w). Both are only editable while no decrement is applied — the
        used quantity because changing it after stock moved would desync the
        reversal, and the cost alongside it so the line's recorded spend can
        never drift from the stock movement it is charged against. Un-mark the
        material first to change either.
        """
        from .services.work_order_material_usage import apply_material_usage

        work_order = self.get_object()
        try:
            usage = work_order.material_usage.select_related(
                "material__inventory_item", "inventory_item"
            ).get(id=material_id)
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

        edits: list[str] = []
        raw_qty = request.data.get("quantity_used")
        if raw_qty is not None:
            try:
                new_qty = Decimal(str(raw_qty))
            except (InvalidOperation, ValueError, TypeError):
                return Response(
                    {"detail": "quantity_used must be a number."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if new_qty < 0:
                return Response(
                    {"detail": "quantity_used cannot be negative."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            # Only editable before the decrement is applied — changing the
            # amount after stock has moved would desync the reversal.
            if usage.applied_quantity is None:
                usage.quantity_used = new_qty
                edits.append("quantity_used")

        if "unit_cost" in request.data:
            raw_cost = request.data["unit_cost"]
            # An explicit null (or a blank multipart field) clears the price —
            # "I don't know what this cost" is a real answer.
            if raw_cost in (None, "", "null"):
                new_cost = None
            else:
                try:
                    new_cost = Decimal(str(raw_cost))
                except (InvalidOperation, ValueError, TypeError):
                    return Response(
                        {"detail": "unit_cost must be a number."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if new_cost < 0:
                    return Response(
                        {"detail": "unit_cost cannot be negative."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            # Same rule as ``quantity_used``: frozen once stock has moved, so
            # the recorded spend can't drift from the decrement it backs.
            if usage.applied_quantity is None:
                usage.unit_cost = new_cost
                edits.append("unit_cost")

        if edits:
            usage.save(update_fields=edits)

        apply_material_usage(usage, was_used=bool(was_used), actor=request.user)
        return Response(WorkOrderMaterialUsageSerializer(usage, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="materials")
    def add_material(self, request, pk=None):
        """Add an ad-hoc material line to this work order (op-768w).

        The only way to record a material on a *corrective* work order, which
        has no PM template to copy rows from — and the way any work order
        records something bought mid-job that nobody planned for.

        Body (``multipart/form-data`` when a receipt is attached):

        ``material_name``
            Required. What was used or bought.
        ``quantity_used``
            Optional, defaults to 1. Also seeds ``quantity_planned`` so the
            line reads consistently next to the template-derived ones.
        ``unit``, ``unit_cost``
            Optional. ``unit_cost`` defaults from the linked inventory item's
            current unit cost when one is given and no cost is supplied — a
            default, not a lock: the caller may override it.
        ``inventory_item``
            Optional. Links the line to tracked stock so marking it used
            decrements that item (see ``toggle_material``). Omit it for an
            out-of-pocket buy: the line then records the spend and moves no
            stock. Adding a material NEVER creates an inventory item.
        ``receipt_image``
            Optional proof-of-purchase photo.

        The line starts un-used — mark it with ``toggle_material`` so the
        decrement goes through the one apply seam.
        """
        work_order = self.get_object()

        form = WorkOrderAdHocMaterialSerializer(data=request.data)
        if not form.is_valid():
            return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)
        data = form.validated_data

        inventory_item = data.get("inventory_item")
        quantity_used = data.get("quantity_used")
        if quantity_used is None:
            quantity_used = Decimal("1.00")

        unit_cost = data.get("unit_cost")
        if unit_cost is None and inventory_item is not None:
            # Seed from what the item currently costs so the tech only types a
            # price when the real one differs. A default, not a lock.
            unit_cost = inventory_item.unit_cost

        usage = WorkOrderMaterialUsage.objects.create(
            work_order=work_order,
            material=None,
            is_ad_hoc=True,
            inventory_item=inventory_item,
            material_name=data["material_name"],
            # Nothing planned this line — it *is* the plan, so both quantities
            # agree and the row reads consistently beside the template ones.
            quantity_planned=quantity_used,
            quantity_used=quantity_used,
            unit=data.get("unit", ""),
            unit_cost=unit_cost,
            receipt_image=data.get("receipt_image"),
        )

        return Response(
            WorkOrderMaterialUsageSerializer(usage, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["delete"], url_path="materials/(?P<material_id>[^/.]+)")
    def remove_material(self, request, pk=None, material_id=None):
        """Delete an ad-hoc material line from this work order (op-768w).

        Two guards, both 400:

        * **Template-derived lines are not deletable** — they are the frozen
          copy of the PM spec and appear on the printed sheet; removing one
          would rewrite what the job was supposed to be. Parity with the
          behaviour before ad-hoc lines existed, where nothing was deletable.
        * **A line with a live stock decrement is not deletable** — deleting it
          would strand the units taken out of inventory with nothing left to
          reverse them. Un-toggle it first, which restores the stock, then
          remove it.
        """
        work_order = self.get_object()
        try:
            usage = work_order.material_usage.get(id=material_id)
        except (WorkOrderMaterialUsage.DoesNotExist, DjangoValidationError, ValueError):
            return Response(
                {"detail": "Material usage record not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not usage.is_ad_hoc:
            return Response(
                {"detail": "Only ad-hoc materials can be removed."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if usage.applied_quantity is not None:
            return Response(
                {
                    "detail": (
                        "This material has stock applied. Un-mark it as used "
                        "first to restore the stock, then remove it."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        usage.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        request=WorkOrderAdHocToolSerializer,
        responses={
            201: WorkOrderToolSerializer,
            400: OpenApiResponse(description="Validation failed (blank name, quantity < 1)."),
            403: OpenApiResponse(description="Write requires staff / Logistics / SIG admin."),
        },
        summary="Add an ad-hoc tool to this work order",
    )
    @action(detail=True, methods=["post"], url_path="tools")
    def add_tool(self, request, pk=None):
        """Add an ad-hoc tool to this work order (op-0v4).

        The only way to record a tool on a *corrective* work order, which has
        no PM template to copy rows from — and the way any work order records
        something the tech turned out to need mid-job.

        Body (JSON):

        ``name``
            Required, non-blank. What to grab.
        ``quantity``
            Optional, defaults to 1. Must be at least 1.
        ``inventory_item``
            Optional. Links the tool to tracked stock so its storage location
            stands in whenever no per-job location is set. Adding a tool NEVER
            creates an inventory item, and never moves stock: a tool is
            gathered, used and returned.
        ``location_hint``
            Optional. Where the tool is staged for THIS job — the one field
            that stays editable afterwards.
        ``is_required``
            Optional, defaults to True.
        ``notes``
            Optional.
        """
        work_order = self.get_object()

        form = WorkOrderAdHocToolSerializer(data=request.data)
        if not form.is_valid():
            return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)
        data = form.validated_data

        tool = WorkOrderTool.objects.create(
            work_order=work_order,
            # No template spec exists for a tool typed in during the job — that
            # is what makes it ad-hoc, and what makes it removable.
            tool=None,
            is_ad_hoc=True,
            inventory_item=data.get("inventory_item"),
            name=data["name"],
            quantity=data.get("quantity", 1),
            location_hint=data.get("location_hint", ""),
            is_required=data.get("is_required", True),
            notes=data.get("notes", ""),
        )

        return Response(
            WorkOrderToolSerializer(tool, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=WorkOrderToolLocationSerializer,
        responses={
            200: WorkOrderToolSerializer,
            204: OpenApiResponse(description="Ad-hoc tool removed."),
            400: OpenApiResponse(
                description=(
                    "PATCH: location_hint missing or too long. "
                    "DELETE: the row is template-derived and cannot be removed."
                )
            ),
            403: OpenApiResponse(description="Write requires staff / Logistics / SIG admin."),
            404: OpenApiResponse(description="No such tool on this work order."),
        },
        summary="Restage (PATCH) or remove (DELETE) one work-order tool",
    )
    @action(detail=True, methods=["patch", "delete"], url_path="tools/(?P<tool_id>[^/.]+)")
    def tool_detail(self, request, pk=None, tool_id=None):
        """Restage or remove one tool on this work order (op-0v4).

        ``PATCH`` sets ``location_hint`` — where the tool is staged for *this*
        job. Allowed on **any** row, template-derived included: per-job
        restaging is the point of the model, and it writes only here, never
        back to the ``MaintenanceTool`` the row was copied from, so the next
        work order off that template still gets the template's location. Blank
        clears the hint and lets the linked inventory item's location stand in
        again.

        ``DELETE`` removes an ad-hoc row. Template-derived rows are not
        deletable (400), exactly as for materials: they are the frozen copy of
        what the job was supposed to need and they appear on the printed sheet.

        Both 404 on a tool id that isn't on this work order — including one
        belonging to a different work order, so a row can't be reached
        sideways.
        """
        work_order = self.get_object()
        try:
            tool = work_order.tools.get(id=tool_id)
        except (WorkOrderTool.DoesNotExist, DjangoValidationError, ValueError):
            return Response(
                {"detail": "Work order tool not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if request.method == "DELETE":
            if not tool.is_ad_hoc:
                return Response(
                    {"detail": "Only ad-hoc tools can be removed."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            tool.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        form = WorkOrderToolLocationSerializer(data=request.data)
        if not form.is_valid():
            return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)

        tool.location_hint = form.validated_data["location_hint"]
        tool.save(update_fields=["location_hint"])
        return Response(WorkOrderToolSerializer(tool, context={"request": request}).data)


class WorkOrderAttachmentViewSet(viewsets.ModelViewSet):
    """API endpoint for the standard work order's attachments list (op-7pjj).

    The internal-WO counterpart of
    ``ThirdPartyWorkOrderAttachmentViewSet`` / the purchase-order attachments
    list: upload, list, and delete arbitrary files hung off one work order.
    Multipart upload; ``uploaded_by`` is stamped server-side.

    Permissions deliberately follow the *parent* ``WorkOrderViewSet``
    (``IsAuthenticatedOrStaffSigAdminWrite``) rather than the third-party
    viewset's read-gated ``IsStaffOrSigAdmin``: standard PM work orders are
    visible to every authenticated volunteer (gh #374), so hiding their
    attachments would leave a maker looking at a work order whose paperwork
    they could not open. Writes and deletes stay staff / Logistics / SIG-admin
    only, matching every other mutation on the work order itself.
    """

    queryset = WorkOrderAttachment.objects.select_related("work_order", "uploaded_by").all()
    serializer_class = WorkOrderAttachmentSerializer
    permission_classes = [IsAuthenticatedOrStaffSigAdminWrite]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_queryset(self):
        """Honor ``?work_order=`` and ``?kind=``.

        Filtered here rather than via ``filterset_fields`` because
        django-filter is not a dependency of this project — same as
        ``AssetDocumentViewSet`` and ``AssetProblemViewSet``.
        """
        qs = super().get_queryset()
        work_order = self.request.query_params.get("work_order")
        if work_order:
            qs = qs.filter(work_order_id=work_order)
        kind = self.request.query_params.get("kind")
        if kind:
            qs = qs.filter(kind=kind)
        return qs

    def perform_create(self, serializer):
        """Stamp the uploader — the serializer keeps the field read-only."""
        if self.request.user and self.request.user.is_authenticated:
            serializer.save(uploaded_by=self.request.user)
        else:
            serializer.save()


class MaintenanceItemViewSet(viewsets.ModelViewSet):
    """API endpoint for asset maintenance items (PM tasks)."""

    queryset = (
        MaintenanceItem.objects.prefetch_related("materials", "tools", "tasks")
        .select_related("asset")
        .all()
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

        ``reorder_qty`` is BASE units — the unit a ``ReorderRequest.quantity``
        is stored in — because the caller FILES it: the maintenance dashboard's
        "Create reorder requests & continue" POSTs this number straight through.
        It is therefore ``base_reorder_quantity``, the one derivation every
        other filing path uses, and not the raw ``reorder_quantity`` column,
        which for a pack-counting item is a count of PACKS and filed a 12th of
        the intended order (``test_reorder_filing.py``).
        """
        item = self.get_object()
        alerts = []
        # ``count_level`` is joined because ``base_reorder_quantity`` reads it
        # (via ``counts_in_packs`` and ``count_at_level``) for every alerted
        # material; the raw column this replaced touched no relation, so
        # without the join each low-stock pack-counting material costs a query.
        materials = item.materials.select_related(
            "inventory_item", "inventory_item__count_level"
        ).all()
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
                    "reorder_qty": base_reorder_quantity(inv),
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

        # A client-supplied due_date arrives as a string (ScanTTY sends one; the
        # web dashboard sends no body at all). Handing that string straight to
        # objects.create() persists fine but leaves a str on the in-memory
        # instance, so WorkOrder.is_overdue compares a date to a str and 500s
        # while serializing the 201 response — after the work order has already
        # been committed (BACKEND-18). Parse it here, outside the atomic block,
        # so a malformed date is a clean 400 before anything is written.
        raw_due_date = request.data.get("due_date")
        if raw_due_date:
            due_date = serializers.DateField().to_internal_value(raw_due_date)
        else:
            due_date = item.next_due_at.date() if item.next_due_at else None

        with transaction.atomic():
            wo = WorkOrder.objects.create(
                maintenance_item=item,
                asset=item.asset,
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
                    quantity_used=mat.quantity,
                    unit=mat.unit,
                )

            # op-0v4: freeze the template's tool list onto the job so it can be
            # restaged per-job without rewriting the recurring template.
            create_work_order_tools(wo)

            # Create a LOTO completion row per energy source on the asset so a
            # scanned-back paper form has rows to apply loto_<id> marks against.
            from .services.work_order_loto import create_loto_completions

            create_loto_completions(wo)

        serializer = WorkOrderSerializer(wo, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated])
    def generate_work_orders_bulk(self, request):
        """Generate work orders for all overdue or due-this-week maintenance items."""
        from .services.work_order_loto import create_loto_completions

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
                    status__in=[WorkOrder.Status.OPEN, WorkOrder.Status.IN_PROGRESS]
                ).exists()
                if existing:
                    continue

                due_date = next_due.date() if next_due else now.date()
                wo = WorkOrder.objects.create(
                    maintenance_item=item,
                    asset=item.asset,
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
                        quantity_used=mat.quantity,
                        unit=mat.unit,
                    )
                create_work_order_tools(wo)
                create_loto_completions(wo)
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
        from .services.work_order_reports import iter_asset_work_orders, prefetch_asset_work_orders

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
            WorkOrder.Status.OPEN,
            WorkOrder.Status.IN_PROGRESS,
            WorkOrder.Status.BLOCKED,
        ]
        # "Unscheduled" = open work that isn't on a recurring interval: a
        # one-off PM (template with no ``interval_days``) or a corrective work
        # order, which has no template at all. The template-less arm is spelled
        # out rather than left to join promotion — this must not depend on how
        # the ORM chooses to join a nullable FK.
        unscheduled_qs = (
            WorkOrder.objects.filter(status__in=open_statuses)
            .filter(
                Q(maintenance_item__isnull=True) | Q(maintenance_item__interval_days__isnull=True)
            )
            .select_related("maintenance_item", "asset")
            .order_by("created_at")
        )
        unscheduled = [
            {
                "workorder_id": str(wo.id),
                "short_id": wo.short_id,
                "asset_id": str(wo.asset_id) if wo.asset_id else None,
                "asset_name": wo.asset.name if wo.asset_id else "",
                "problem": wo.display_title,
                "opened_at": wo.created_at.isoformat(),
                "status": wo.status,
            }
            for wo in unscheduled_qs
        ]

        completed_qs = (
            WorkOrder.objects.filter(
                status=WorkOrder.Status.COMPLETED,
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
            # No template means no estimate to draw on: cost such a work order
            # from the materials actually used, same as a one-off PM.
            if mi is not None and mi.interval_days is not None:
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

        assets = prefetch_asset_work_orders(
            Asset.objects.all(),
            WorkOrder.objects.prefetch_related("material_usage__material"),
        )
        by_asset = []
        for asset in assets:
            days_set: set = set()
            total_cost = Decimal("0.00")
            for wo, mi in iter_asset_work_orders(asset):
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
                    wo.status == WorkOrder.Status.COMPLETED
                    and wo.completed_at is not None
                    and window_start_date <= wo.completed_at.date() <= today
                ):
                    if mi is not None and mi.interval_days is not None:
                        total_cost += mi.estimated_cost or Decimal("0.00")
                    else:
                        for usage in wo.material_usage.all():
                            if usage.was_used and usage.material is not None:
                                total_cost += (
                                    usage.quantity_planned * usage.material.estimated_cost_per_unit
                                )
            if asset.status == Asset.Status.MAINTENANCE:
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
            WorkOrder.Status.OPEN,
            WorkOrder.Status.IN_PROGRESS,
            WorkOrder.Status.BLOCKED,
        ]
        open_problem_statuses = [
            AssetProblem.Status.REPORTED,
            AssetProblem.Status.IN_PROGRESS,
        ]
        open_location_problem_statuses = [
            LocationProblem.Status.REPORTED,
            LocationProblem.Status.IN_PROGRESS,
        ]

        rows = []

        for wo in (
            WorkOrder.objects.filter(status__in=open_wo_statuses)
            .select_related("maintenance_item", "asset")
            # display_title falls back to the promoted report on a corrective WO
            .prefetch_related("asset_problems")
            .order_by("-created_at")
        ):
            rows.append(
                {
                    "kind": "work_order",
                    "id": str(wo.id),
                    "short_id": wo.short_id,
                    "title": wo.display_title,
                    "status": wo.status,
                    "status_display": wo.get_status_display(),
                    "asset_id": str(wo.asset_id) if wo.asset_id else None,
                    "asset_name": wo.asset.name if wo.asset_id else "",
                    "location_id": None,
                    "location_name": None,
                    "severity": None,
                    "due_date": wo.due_date.isoformat() if wo.due_date else None,
                    "opened_at": wo.created_at.isoformat(),
                }
            )

        # A promoted problem is already in this feed as its work order, so only
        # un-promoted reports appear here (same rule as LocationProblem below).
        for ap in (
            AssetProblem.objects.filter(
                status__in=open_problem_statuses,
                work_order__isnull=True,
                third_party_work_order__isnull=True,
            )
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


class _CostRecoveryCSVRenderer(BaseRenderer):
    """Passthrough renderer registering the ``csv`` format for content negotiation.

    ``AssetReportViewSet.cost_recovery`` returns a fully-formed ``HttpResponse``
    for the CSV/PDF formats, so ``render`` is never invoked — these renderers
    exist only so DRF accepts ``?format=csv`` / ``?format=pdf`` (its reserved
    format query param) instead of 404-ing on an unregistered format.
    """

    media_type = "text/csv"
    format = "csv"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


class _CostRecoveryPDFRenderer(BaseRenderer):
    """Passthrough renderer registering the ``pdf`` format (see CSV renderer)."""

    media_type = "application/pdf"
    format = "pdf"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


class AssetReportViewSet(viewsets.ViewSet):
    """API endpoint for asset reports."""

    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["get"])
    def assets_by_status(self, request):
        """Get count of assets grouped by status."""
        from django.db.models import Count

        queryset = Asset.objects.values("status").annotate(count=Count("id")).order_by("status")

        data = []
        status_choices = dict(Asset.Status.choices)
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
        assets_in_maintenance = Asset.objects.filter(
            status=Asset.Status.MAINTENANCE
        ).select_related("category", "location")

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
          maintenance via internal WorkOrder + WorkOrderMaterialUsage. Priced
          from the materials actually paid for (``WorkOrderMaterialUsage.unit_cost``,
          op-768w) where that was captured, falling back to the template/material
          estimate otherwise. This is internal analytics, so it is *not* gated on
          ``Asset.is_cost_recoverable`` the way the cost-recovery statement is.
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
        from .services.work_order_reports import (
            iter_asset_work_orders,
            prefetch_asset_work_orders,
            wo_material_cost,
        )

        today = timezone.now().date()
        window_start = today - timedelta(days=90)

        vendor_link_qs = ThirdPartyWorkOrderAsset.objects.select_related("work_order").filter(
            work_order__status=ThirdPartyWorkOrder.STATUS_CLOSED,
            work_order__closed_at__date__gte=window_start,
            work_order__closed_at__date__lte=today,
        )

        assets = prefetch_asset_work_orders(
            Asset.objects.all(),
            WorkOrder.objects.prefetch_related("material_usage__material"),
        ).prefetch_related(
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

            for wo, mi in iter_asset_work_orders(asset):
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
                    wo.status == WorkOrder.Status.COMPLETED
                    and wo.completed_at is not None
                    and window_start <= wo.completed_at.date() <= today
                ):
                    # Corrective work has no template estimate, so it costs
                    # like a one-off PM: the materials it actually consumed.
                    cost, _is_actual = wo_material_cost(mi, wo)
                    if mi is not None and mi.interval_days is not None:
                        scheduled += cost
                    else:
                        unscheduled += cost

            vendor = Decimal("0.00")
            for link in getattr(asset, "_tco_vendor_links", []):
                if link.allocated_cost is not None:
                    vendor += link.allocated_cost
                    wo_closed = link.work_order.closed_at
                    if wo_closed is not None:
                        days_set.add(wo_closed.date())

            if asset.status == Asset.Status.MAINTENANCE:
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
          while closing an internal work order, preventive or corrective.
          Sourced from ``WorkOrderMaterialUsage`` rows with ``was_used=True``,
          reached via ``iter_asset_work_orders`` (both the PM-template path and
          the direct asset FK) and windowed by the ``completed_at`` date (the same field
          ``tco`` uses). Extra keys: ``quantity`` (planned qty), ``unit``,
          ``work_order_id``, ``estimated_cost`` (``quantity`` ×
          ``material.estimated_cost_per_unit``; null if the material was
          deleted after the work order was created, or the line is ad-hoc and
          has no template spec), ``actual_cost`` (``quantity_used`` ×
          ``unit_cost`` — the op-768w capture; null when the line was never
          priced) and ``cost``, the one figure to report: the actual where it
          exists, the estimate otherwise.

        Query params ``start_date``/``end_date`` (``YYYY-MM-DD``) default to the
        last 30 days. Rows are sorted by ``asset_name`` then ``used_at``.
        """
        from .services.work_order_reports import iter_asset_work_orders, prefetch_asset_work_orders

        start_date, end_date = self._supplies_window(request)

        rows = []

        # Serialized usage: ComponentUsageEvent tied to an asset. Only install
        # and consume count as "put into service on / used up by" the asset —
        # receive/remove/retire/dispose are stock or teardown events.
        events = ComponentUsageEvent.objects.filter(
            asset__isnull=False,
            action__in=[
                SerializedComponent.Action.INSTALL,
                SerializedComponent.Action.CONSUME,
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

        # Consumable usage: materials marked used on a completed internal work
        # order, preventive or corrective. Same prefetch traversal tco uses, so
        # this stays a fixed number of queries.
        assets = prefetch_asset_work_orders(
            Asset.objects.all(),
            WorkOrder.objects.prefetch_related("material_usage__material"),
        )
        for asset in assets:
            for wo, _mi in iter_asset_work_orders(asset):
                if wo.status != WorkOrder.Status.COMPLETED or wo.completed_at is None:
                    continue
                completed_date = wo.completed_at.date()
                if not (start_date <= completed_date <= end_date):
                    continue
                for usage in wo.material_usage.all():
                    # Consumption, not cost: this listing answers "what was drawn
                    # for this asset", so it stays on ``was_used`` and does NOT
                    # take op-4pzp's "a priced ad-hoc line counts on entry" rule
                    # (that governs ``WorkOrder.actual_material_cost``). There is
                    # no total here for the two to disagree about — every row is
                    # one material line.
                    if not usage.was_used:
                        continue
                    estimated_cost = None
                    if usage.material is not None:
                        estimated_cost = (
                            usage.quantity_planned * usage.material.estimated_cost_per_unit
                        ).quantize(Decimal("0.01"))
                    # Real money beats the estimate line by line here — unlike
                    # the work-order-level reports, every row is one material.
                    actual_cost = usage.actual_cost
                    if actual_cost is not None:
                        actual_cost = actual_cost.quantize(Decimal("0.01"))
                    cost = actual_cost if actual_cost is not None else estimated_cost
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
                            "estimated_cost": (
                                None if estimated_cost is None else str(estimated_cost)
                            ),
                            "actual_cost": None if actual_cost is None else str(actual_cost),
                            "cost": None if cost is None else str(cost),
                        }
                    )

        # Sort on the aware datetimes, then serialize used_at to ISO-8601.
        rows.sort(key=lambda r: (r["asset_name"], r["used_at"]))
        for row in rows:
            row["used_at"] = row["used_at"].isoformat()

        return Response(rows)

    @staticmethod
    def _cost_recovery_selection(request):
        """Parse and validate the asset selection for cost_recovery.

        Selection params (all optional individually, but at least one is
        required):

        * ``asset_ids`` — Asset UUIDs, repeated and/or comma-joined.
        * ``category_ids`` — integer Category PKs, repeated and/or comma-joined.
        * ``all_assets`` — ``"true"``/``"1"`` to run across every asset.
        * ``ownership_type`` — one of ``Asset.OwnershipType.values``
          (user/group/space).
        * ``owning_group`` — integer ``auth.Group`` PK (the SIG/committee).

        Requiring at least one keeps a bare request from silently running an
        unbounded statement; ``all_assets=true`` is the explicit escape hatch.

        Returns a dict with keys ``asset_ids`` (UUID strings), ``category_ids``
        (ints), ``all_assets`` (bool), ``ownership_type`` (str or None) and
        ``owning_group`` (int or None). Raises DRF ValidationError on malformed
        values or an empty selection.
        """
        import uuid

        def _collect(param):
            values = []
            for chunk in request.query_params.getlist(param):
                values.extend(piece.strip() for piece in chunk.split(",") if piece.strip())
            return values

        raw_asset_ids = _collect("asset_ids")
        raw_category_ids = _collect("category_ids")

        all_assets = (request.query_params.get("all_assets") or "").strip().lower() in {
            "true",
            "1",
        }

        ownership_type = (request.query_params.get("ownership_type") or "").strip() or None
        if ownership_type is not None and ownership_type not in Asset.OwnershipType.values:
            allowed = ", ".join(Asset.OwnershipType.values)
            raise serializers.ValidationError({"ownership_type": f"Must be one of {allowed}."})

        raw_owning_group = (request.query_params.get("owning_group") or "").strip() or None
        owning_group = None
        if raw_owning_group is not None:
            try:
                owning_group = int(raw_owning_group)
            except (ValueError, TypeError):
                raise serializers.ValidationError(
                    {"owning_group": f"Invalid group id: {raw_owning_group!r}."}
                )

        if not (
            raw_asset_ids
            or raw_category_ids
            or all_assets
            or ownership_type is not None
            or owning_group is not None
        ):
            raise serializers.ValidationError(
                "Select at least one asset (asset_ids), category (category_ids), or "
                "ownership filter (ownership_type / owning_group) — or pass "
                "all_assets=true to run the statement across every asset."
            )

        asset_ids = []
        for value in raw_asset_ids:
            try:
                asset_ids.append(str(uuid.UUID(value)))
            except (ValueError, AttributeError, TypeError):
                raise serializers.ValidationError({"asset_ids": f"Invalid asset id: {value!r}."})

        category_ids = []
        for value in raw_category_ids:
            try:
                category_ids.append(int(value))
            except (ValueError, TypeError):
                raise serializers.ValidationError(
                    {"category_ids": f"Invalid category id: {value!r}."}
                )

        return {
            "asset_ids": asset_ids,
            "category_ids": category_ids,
            "all_assets": all_assets,
            "ownership_type": ownership_type,
            "owning_group": owning_group,
        }

    @staticmethod
    def _cost_recovery_asset_queryset(selection):
        """Resolve the selected assets for cost_recovery from a parsed selection.

        ``all_assets`` or either ownership filter widens the base set to every
        asset (an ownership filter alone therefore means "all assets with that
        ownership"); otherwise the base set is the explicit ``asset_ids`` UNION
        the ``category_ids`` expansion, de-duped. The ownership filters are then
        applied on top of whichever base set was chosen.
        """
        if (
            selection["all_assets"]
            or selection["ownership_type"] is not None
            or selection["owning_group"] is not None
        ):
            queryset = Asset.objects.all()
        else:
            selected_ids = set(selection["asset_ids"])
            if selection["category_ids"]:
                selected_ids.update(
                    str(pk)
                    for pk in Asset.objects.filter(
                        category_id__in=selection["category_ids"]
                    ).values_list("id", flat=True)
                )
            queryset = Asset.objects.filter(id__in=selected_ids)

        if selection["ownership_type"] is not None:
            queryset = queryset.filter(ownership_type=selection["ownership_type"])
        if selection["owning_group"] is not None:
            queryset = queryset.filter(owning_group_id=selection["owning_group"])
        return queryset

    @staticmethod
    def _cost_recovery_window(request):
        """Resolve the cost_recovery reporting window.

        Either a ``period`` preset (``past_week``/``past_month``/``past_year`` —
        trailing windows of 7/30/365 days ending today) OR an explicit
        ``start_date`` & ``end_date`` pair (``YYYY-MM-DD``). Returns
        ``(start_date, end_date, period_label)`` where ``period_label`` is the
        preset name, or ``None`` for a custom range. Raises DRF ValidationError
        on malformed or missing input.
        """
        from datetime import datetime

        presets = {
            "past_week": timedelta(days=7),
            "past_month": timedelta(days=30),
            "past_year": timedelta(days=365),
        }
        today = timezone.now().date()

        period = request.query_params.get("period")
        if period:
            span = presets.get(period)
            if span is None:
                raise serializers.ValidationError(
                    {"period": "Must be one of past_week, past_month, past_year."}
                )
            return today - span, today, period

        start_str = request.query_params.get("start_date")
        end_str = request.query_params.get("end_date")
        if start_str and end_str:
            try:
                start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
                end_date = datetime.strptime(end_str, "%Y-%m-%d").date()
            except ValueError:
                raise serializers.ValidationError(
                    {"start_date": "start_date and end_date must be YYYY-MM-DD."}
                )
            if start_date > end_date:
                raise serializers.ValidationError(
                    {"start_date": "start_date must not be after end_date."}
                )
            return start_date, end_date, None

        raise serializers.ValidationError(
            "Provide either 'period' (past_week/past_month/past_year) or both "
            "'start_date' and 'end_date' (YYYY-MM-DD)."
        )

    @staticmethod
    def _pm_estimated_cost(maintenance_item, work_order):
        """Estimated cost of one completed internal PM work order.

        Thin alias for
        :func:`inventory.services.work_order_reports.wo_estimated_material_cost`,
        which ``tco`` shares — see there for the scheduled-vs-one-off rule. Kept
        as a method so the report's own cost walk reads in one place.
        """
        from .services.work_order_reports import wo_estimated_material_cost

        return wo_estimated_material_cost(maintenance_item, work_order)

    @staticmethod
    def _cost_recovery_csv(asset_blocks):
        """Flat one-row-per-service CSV for the cost-recovery report.

        Every selected asset appears; an asset with no in-window services emits
        a single row with the service columns left blank. ``cost_recoverable``
        (per asset) and ``internal_cost`` (per row) carry the recoverable-vs-
        internal split: ``actual_cost`` is the landlord-billable column and only
        picks up in-house work on a recoverable asset, while ``internal_cost``
        reports what the in-house work cost either way.
        """
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="asset_cost_recovery.csv"'
        fieldnames = [
            "asset_tag",
            "serial_number",
            "status",
            "date_received",
            "cost_recoverable",
            "service_date",
            "source",
            "description",
            "estimated_cost",
            "internal_cost",
            "actual_cost",
        ]
        writer = csv.DictWriter(response, fieldnames=fieldnames)
        writer.writeheader()
        for block in asset_blocks:
            base = {
                "asset_tag": block["asset_tag"],
                "serial_number": block["serial_number"],
                "status": block["status"],
                "date_received": (
                    block["date_received"].isoformat() if block["date_received"] else ""
                ),
                "cost_recoverable": "yes" if block["is_cost_recoverable"] else "no",
            }
            services = block["services"]
            if not services:
                writer.writerow(
                    {
                        **base,
                        "service_date": "",
                        "source": "",
                        "description": "",
                        "estimated_cost": "",
                        "internal_cost": "",
                        "actual_cost": "",
                    }
                )
                continue
            for svc in services:
                writer.writerow(
                    {
                        **base,
                        "service_date": svc["date"].isoformat(),
                        "source": svc["source"],
                        "description": svc["description"],
                        "estimated_cost": (
                            "" if svc["estimated_cost"] is None else f'{svc["estimated_cost"]:.2f}'
                        ),
                        "internal_cost": (
                            "" if svc["internal_cost"] is None else f'{svc["internal_cost"]:.2f}'
                        ),
                        "actual_cost": (
                            "" if svc["actual_cost"] is None else f'{svc["actual_cost"]:.2f}'
                        ),
                    }
                )
        return response

    @action(
        detail=False,
        methods=["get"],
        renderer_classes=[JSONRenderer, _CostRecoveryCSVRenderer, _CostRecoveryPDFRenderer],
    )
    def cost_recovery(self, request):
        """Per-asset cost-recovery statement billed to the landlord.

        Select assets/categories + a period and get, per asset, an itemized
        service history with Estimated (internal), Internal (what in-house work
        really cost) and Actual (the landlord-billable column) figures. The
        Actual total is the recoverable amount.

        Query params:
        - Selection (>=1 required): ``asset_ids`` (Asset UUIDs) and/or
          ``category_ids`` (integer Category PKs); each repeatable and/or
          comma-joined. Categories expand to ``category.assets`` and the union
          is de-duped. ``all_assets=true`` runs across every asset;
          ``ownership_type`` (user/group/space) and ``owning_group`` (auth.Group
          PK — the SIG/committee) scope by asset ownership and likewise widen
          the base set to every asset before filtering.
        - Period (one of): ``period`` in {past_week, past_month, past_year}
          (trailing window ending today) OR ``start_date`` & ``end_date``
          (YYYY-MM-DD).
        - ``format`` in {json (default), csv, pdf}.

        Service sources:
        - ``pm`` — completed internal WorkOrders. ``estimated_cost`` is the
          template/material estimate as always; ``internal_cost`` is what the
          job really cost (``WorkOrderMaterialUsage.unit_cost`` where captured,
          the estimate otherwise); ``actual_cost`` — the billable column — is
          that internal figure **only for an asset flagged
          ``is_cost_recoverable``**, and only when it is a real actual. An
          estimate is never billed as an actual, so a work order that predates
          cost capture keeps reporting exactly its old numbers.
        - ``vendor`` — closed ThirdPartyWorkOrder allocations to the asset
          (actual = allocated_cost; the recoverable spend). Vendor invoices are
          landlord-billable on every asset, flagged or not.
        - ``manual`` — MaintenanceRecord rows (actual = recorded cost).
        """
        from django.db.models import Prefetch

        from maintenance_orders.models import ThirdPartyWorkOrder, ThirdPartyWorkOrderAsset

        from .serializers import AssetCostRecoveryReportSerializer
        from .services.work_order_reports import (
            iter_asset_work_orders,
            prefetch_asset_work_orders,
            wo_actual_material_cost,
        )

        selection = self._cost_recovery_selection(request)
        asset_ids = selection["asset_ids"]
        category_ids = selection["category_ids"]
        start_date, end_date, period_label = self._cost_recovery_window(request)

        # ``format`` is DRF's reserved content-negotiation query param; the
        # action-scoped renderers above register json/csv/pdf so it negotiates
        # cleanly (an unknown format 404s in negotiation before we get here).
        fmt = request.accepted_renderer.format

        # Resolve the selected asset set (ids/categories/all + ownership).
        selected_qs = self._cost_recovery_asset_queryset(selection)

        # Window-scoped prefetch querysets — one pass per source, no N+1.
        pm_wo_qs = WorkOrder.objects.filter(
            status=WorkOrder.Status.COMPLETED,
            completed_at__date__gte=start_date,
            completed_at__date__lte=end_date,
        ).prefetch_related("material_usage__material")
        vendor_link_qs = ThirdPartyWorkOrderAsset.objects.select_related(
            "work_order", "work_order__vendor"
        ).filter(
            work_order__status=ThirdPartyWorkOrder.STATUS_CLOSED,
            work_order__closed_at__date__gte=start_date,
            work_order__closed_at__date__lte=end_date,
        )
        manual_qs = MaintenanceRecord.objects.select_related("vendor").filter(
            completed_on__gte=start_date,
            completed_on__lte=end_date,
        )

        assets = (
            prefetch_asset_work_orders(
                selected_qs.select_related("category"),
                pm_wo_qs,
            )
            .prefetch_related(
                Prefetch(
                    "third_party_work_order_links",
                    queryset=vendor_link_qs,
                    to_attr="_cr_vendor_links",
                ),
                Prefetch("maintenance_records", queryset=manual_qs, to_attr="_cr_manual_records"),
            )
            .order_by("name")
        )

        asset_blocks = []
        grand_estimated = Decimal("0.00")
        grand_internal = Decimal("0.00")
        grand_actual = Decimal("0.00")
        service_count = 0

        for asset in assets:
            services = []
            # Internal work, preventive (from a template) and corrective alike.
            # ``internal_cost`` is the in-house figure on every asset: real
            # money where it was recorded, the estimate otherwise — which is
            # what keeps a work order that predates cost capture reporting
            # exactly its old numbers. Only a real actual, and only on a
            # recoverable asset, reaches the billable ``actual_cost`` column.
            for wo, mi in iter_asset_work_orders(asset):
                estimated_cost = self._pm_estimated_cost(mi, wo)
                actual_cost = wo_actual_material_cost(wo)
                services.append(
                    {
                        "date": wo.completed_at.date(),
                        "source": "pm",
                        "description": wo.display_title,
                        "estimated_cost": estimated_cost,
                        "internal_cost": (estimated_cost if actual_cost is None else actual_cost),
                        "actual_cost": actual_cost if asset.is_cost_recoverable else None,
                    }
                )
            # Vendor — actual = per-asset allocated_cost (the recoverable spend).
            for link in getattr(asset, "_cr_vendor_links", []):
                wo = link.work_order
                vendor_name = wo.vendor.name if wo.vendor_id else ""
                description = f"{wo.title} ({vendor_name})" if vendor_name else wo.title
                services.append(
                    {
                        "date": wo.closed_at.date(),
                        "source": "vendor",
                        "description": description,
                        "estimated_cost": None,
                        "internal_cost": None,
                        "actual_cost": link.allocated_cost,
                    }
                )
            # Manual — actual = recorded cost (may be null when unknown).
            for record in getattr(asset, "_cr_manual_records", []):
                vendor_name = record.vendor.name if record.vendor_id else ""
                description = f"{record.title} ({vendor_name})" if vendor_name else record.title
                services.append(
                    {
                        "date": record.completed_on,
                        "source": "manual",
                        "description": description,
                        "estimated_cost": None,
                        "internal_cost": None,
                        "actual_cost": record.cost,
                    }
                )

            services.sort(key=lambda s: s["date"])
            subtotal_estimated = sum(
                (s["estimated_cost"] or Decimal("0.00") for s in services), Decimal("0.00")
            )
            subtotal_internal = sum(
                (s["internal_cost"] or Decimal("0.00") for s in services), Decimal("0.00")
            )
            subtotal_actual = sum(
                (s["actual_cost"] or Decimal("0.00") for s in services), Decimal("0.00")
            )
            grand_estimated += subtotal_estimated
            grand_internal += subtotal_internal
            grand_actual += subtotal_actual
            service_count += len(services)

            asset_blocks.append(
                {
                    "asset_id": str(asset.id),
                    "asset_tag": asset.asset_tag or "",
                    "name": asset.name,
                    "serial_number": asset.serial_number or "",
                    "date_received": asset.date_received,
                    "status": asset.status,
                    "status_display": asset.get_status_display(),
                    "category": asset.category.name if asset.category_id else None,
                    "is_cost_recoverable": asset.is_cost_recoverable,
                    "services": services,
                    "subtotal_estimated": subtotal_estimated,
                    "subtotal_internal": subtotal_internal,
                    "subtotal_actual": subtotal_actual,
                }
            )

        if fmt == "csv":
            return self._cost_recovery_csv(asset_blocks)

        if fmt == "pdf":
            from django.contrib.auth.models import Group

            from .utils.cost_recovery_pdf import generate_cost_recovery_pdf

            owning_group_name = None
            if selection["owning_group"] is not None:
                owning_group_name = (
                    Group.objects.filter(pk=selection["owning_group"])
                    .values_list("name", flat=True)
                    .first()
                )

            report = {
                "period": period_label,
                "start_date": start_date,
                "end_date": end_date,
                "generated_at": timezone.now(),
                "asset_ids": asset_ids,
                "category_ids": category_ids,
                "all_assets": selection["all_assets"],
                "ownership_type": selection["ownership_type"],
                "owning_group": selection["owning_group"],
                "owning_group_name": owning_group_name,
                "asset_count": len(asset_blocks),
                "service_count": service_count,
                "grand_total_estimated": grand_estimated,
                "grand_total_internal": grand_internal,
                "grand_total_actual": grand_actual,
                "assets": asset_blocks,
            }
            pdf_bytes = generate_cost_recovery_pdf(report)
            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = 'attachment; filename="asset_cost_recovery.pdf"'
            return response

        # JSON (default). Decimals render as strings for parity with tco.
        payload = {
            "period": period_label,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "asset_ids": asset_ids,
            "category_ids": category_ids,
            "all_assets": selection["all_assets"],
            "ownership_type": selection["ownership_type"],
            "owning_group": selection["owning_group"],
            "asset_count": len(asset_blocks),
            "service_count": service_count,
            "grand_total_estimated": f"{grand_estimated:.2f}",
            "grand_total_internal": f"{grand_internal:.2f}",
            "grand_total_actual": f"{grand_actual:.2f}",
            "assets": AssetCostRecoveryReportSerializer(asset_blocks, many=True).data,
        }
        return Response(payload)

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
                "actual_cost",
                "cost",
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
        status=WorkOrderSubmission.Status.RECEIVED,
        source=(WorkOrderSubmission.Source.SCAN if is_scan else WorkOrderSubmission.Source.EMAIL),
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


def _user_can_charge_item(user, item):
    """Return True if `user` may charge a committee for consuming `item`.

    Charging posts money to the accounting ledger, so it is gated tighter than
    plain usage logging (which is public): staff, superusers, and SIG admins of
    the item's owning_group are allowed. Kept intentionally simple for the
    Phase-2 first cut — tighten later.
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


def _apply_reconciliation_row(
    user,
    item,
    actual_count,
    reason,
    notes="",
    skip_reorder=False,
    at_level=False,
    open_count=None,
):
    """Apply a single reconciliation row. Caller owns transaction + permission check.

    ``actual_count`` is BASE units — unchanged, and what every caller before
    op-ev14 sends — unless ``at_level`` says it is a count of whole packs of the
    item's ``count_level`` ("I counted 3 cases"), which is converted before it
    reaches ``current_stock``. ``open_count`` optionally sets the ``open_closed``
    open-container tally in the same write; that pack count plus the open tally
    is the sealed/open pair that fully describes such an item.

    The stored audit row stays BASE-unit canonical — ``projected_count``,
    ``actual_count`` and ``delta`` are always base units, whatever unit the
    entry arrived in — so the reconciliation history is comparable across a
    later count-mode change.

    Returns (StockReconciliation, reorder_created_bool). Raises
    ``DjangoValidationError`` for a quantity the item's packaging cannot express.
    """
    projected = int(item.current_stock)
    actual = resolve_base_quantity(item, int(actual_count), at_level=at_level)
    delta = actual - projected
    item.current_stock = actual

    update_fields = ["current_stock", "updated_at"]
    if open_count is not None:
        if item.count_mode != InventoryItem.CountMode.OPEN_CLOSED:
            raise DjangoValidationError(
                f"'{item.name}' does not track open containers (count mode "
                f"'{item.count_mode}'); omit open_count."
            )
        item.open_container_count = int(open_count)
        update_fields.append("open_container_count")
    item.save(update_fields=update_fields)

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
    #
    # Both the trigger and the quantity are read at the granularity the item is
    # counted in (op-es7c). For an ``each`` item — every item that exists today —
    # ``count_at_level`` IS ``current_stock`` (just set to ``actual``) and the
    # multiplier is 1, so this is byte-for-byte the previous comparison and the
    # previous quantity. For an item counted in whole packs, ``minimum_stock``
    # and ``reorder_quantity`` are amounts in ITS packs, so comparing raw base
    # units against them — or storing a pack count as if it were base units —
    # would be wrong in both directions.
    if not skip_reorder and not item.is_retired and count_at_level(item) <= item.minimum_stock:
        from reorder_queue.models import ReorderRequest

        requested_by = (user.get_full_name() or user.username).strip()
        base_units_per_count = item.count_level.base_units if counts_in_packs(item) else 1
        reorder_quantity = (item.reorder_quantity or 1) * base_units_per_count
        # Report the trigger in the unit it was judged in. For an ``each`` item
        # that is the previous sentence verbatim; naming the pack for the others
        # keeps the note from reading a base count against a pack threshold.
        if counts_in_packs(item):
            unit = item.count_level.name
            trigger = f"actual={count_at_level(item)} {unit}, minimum={item.minimum_stock} {unit}"
        else:
            trigger = f"actual={actual}, minimum={item.minimum_stock}"
        reorder = ReorderRequest.objects.create(
            item=item,
            quantity=reorder_quantity,
            requested_by=requested_by,
            request_notes=f"Auto-created by stock reconciliation ({trigger}).",
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
            # ``count_level`` joins for the grid's count-unit columns (op-ev14);
            # without it every pack-counting row costs a query.
            .select_related("owning_group", "count_level").order_by("name")
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
                        at_level=bool(row.get("at_level", False)),
                        open_count=row.get("open_count"),
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

        valid_reasons = {v for v, _ in StockReconciliation.ReasonCode.choices}

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
            # Optional columns (op-ev14): ``at_level`` reads ``actual_count`` as
            # a pack count (absent/blank = base units, as before), and
            # ``open_count`` sets an ``open_closed`` item's open tally. Shared
            # parsing with the JSON paths, so "false" in a spreadsheet cell means
            # false rather than a truthy non-empty string.
            try:
                at_level = parse_at_level((row.get("at_level") or "").strip())
            except DjangoValidationError as exc:
                errors.append({"row": row_num, "error": exc.messages[0]})
                continue
            open_count = None
            raw_open = (row.get("open_count") or "").strip()
            if raw_open:
                try:
                    open_count = int(raw_open)
                except (TypeError, ValueError):
                    errors.append(
                        {
                            "row": row_num,
                            "error": "open_count must be a non-negative integer.",
                        }
                    )
                    continue
                if open_count < 0:
                    errors.append({"row": row_num, "error": "open_count must be >= 0."})
                    continue
            parsed.append(
                {
                    "row": row_num,
                    "item_pk": item.pk,
                    "actual": actual,
                    "reason": reason,
                    "notes": row.get("notes") or "",
                    "skip_reorder": skip_reorder,
                    "at_level": at_level,
                    "open_count": open_count,
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
                        at_level=p["at_level"],
                        open_count=p["open_count"],
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
            # The count-mode fields ride along (select_related + only) so the
            # per-row unit columns below cost no extra query on a streamed export.
            .select_related("count_level")
            .only(
                "id",
                "sku",
                "name",
                "current_stock",
                "minimum_stock",
                "base_unit",
                "count_mode",
                "open_container_count",
                "count_level__name",
                "count_level__base_units",
            )
            .order_by("name")
            .iterator(chunk_size=500)
        )

        # ``projected`` stays base-unit canonical; ``count_unit`` /
        # ``projected_at_unit`` name the unit a counter should write
        # ``actual_count`` in (op-ev14), and ``open_count`` round-trips an
        # ``open_closed`` item's open tally. For an ``each`` item the unit is its
        # base unit and ``projected_at_unit`` equals ``projected``.
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
            # Appended, not inserted: every pre-op-ev14 column keeps its index,
            # so a consumer reading this template positionally is unaffected —
            # and ``upload_csv`` matches on header NAMES, so the round trip works
            # either way.
            "count_unit",
            "projected_at_unit",
            "open_count",
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
                        "",  # actual_count
                        "",  # reason
                        "",  # notes
                        "",  # skip_reorder
                        count_unit(item),
                        count_at_level(item),
                        # Only an ``open_closed`` item accepts open_count back on
                        # upload, so only it gets the column pre-filled.
                        (
                            item.open_container_count
                            if item.count_mode == InventoryItem.CountMode.OPEN_CLOSED
                            else ""
                        ),
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
        return self._run_lifecycle_action(request, SerializedComponent.Action.RECEIVE)

    @action(detail=True, methods=["post"])
    def install(self, request, pk=None):
        """Install the unit into an asset (requires ``asset``)."""
        return self._run_lifecycle_action(request, SerializedComponent.Action.INSTALL)

    @action(detail=True, methods=["post"])
    def remove(self, request, pk=None):
        """Remove a reusable unit from its asset (``installed`` -> ``removed``)."""
        return self._run_lifecycle_action(request, SerializedComponent.Action.REMOVE)

    @action(detail=True, methods=["post"])
    def consume(self, request, pk=None):
        """Mark a consumable unit as used up (``installed`` -> ``consumed``)."""
        return self._run_lifecycle_action(request, SerializedComponent.Action.CONSUME)

    @action(detail=True, methods=["post"])
    def retire(self, request, pk=None):
        """Retire a reusable unit from service (-> ``retired``)."""
        return self._run_lifecycle_action(request, SerializedComponent.Action.RETIRE)

    @action(detail=True, methods=["post"])
    def dispose(self, request, pk=None):
        """Dispose the unit (requires ``disposal_reason``; -> ``disposed``)."""
        return self._run_lifecycle_action(request, SerializedComponent.Action.DISPOSE)

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
                component.apply_action(SerializedComponent.Action.RECEIVE, actor=actor)

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
