"""
API views for dashboard configuration management.
"""

from django.http import JsonResponse

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .models import DashboardConfig, DashboardMessage, DashboardWidget
from .serializers import DashboardWidgetSerializer


@api_view(["GET"])
@permission_classes([AllowAny])
def get_dashboard_messages(request):
    """
    Get active dashboard messages for rotation.

    Public endpoint used by TV Dashboard to fetch current messages.
    """
    try:
        # Get active messages in order
        messages = DashboardMessage.objects.filter(is_active=True)
        message_list = [msg.message for msg in messages]

        # Get configuration
        config = DashboardConfig.get_config()

        # Fallback to default if no messages configured
        if not message_list:
            message_list = [
                "Tracking items from request to delivery",
                "Scan QR codes to request reorders",
                "Keeping your makerspace stocked",
            ]

        return Response(
            {
                "messages": message_list,
                "rotation_interval_seconds": config.rotation_interval_seconds,
                "auto_refresh_seconds": config.auto_refresh_seconds,
                "maintenance_mode": config.is_maintenance_mode,
                "maintenance_message": (
                    config.maintenance_message if config.is_maintenance_mode else None
                ),
                "last_updated": config.updated_at.isoformat(),
            }
        )

    except Exception as e:
        # Graceful fallback for any errors
        return Response(
            {
                "messages": ["Tracking items from request to delivery"],
                "rotation_interval_seconds": 10,
                "auto_refresh_seconds": 30,
                "maintenance_mode": False,
                "maintenance_message": None,
                "error": str(e),
            }
        )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_dashboard_config(request):
    """
    Get current dashboard configuration.

    Admin endpoint for viewing current settings.
    """
    try:
        config = DashboardConfig.get_config()
        messages = DashboardMessage.objects.all().order_by("order", "created_at")

        return Response(
            {
                "config": {
                    "rotation_interval_seconds": config.rotation_interval_seconds,
                    "auto_refresh_seconds": config.auto_refresh_seconds,
                    "is_maintenance_mode": config.is_maintenance_mode,
                    "maintenance_message": config.maintenance_message,
                    "custom_css": config.custom_css,
                    "updated_at": config.updated_at.isoformat(),
                },
                "messages": [
                    {
                        "id": msg.id,
                        "message": msg.message,
                        "is_active": msg.is_active,
                        "order": msg.order,
                        "created_at": msg.created_at.isoformat(),
                        "updated_at": msg.updated_at.isoformat(),
                    }
                    for msg in messages
                ],
            }
        )

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def update_dashboard_config(request):
    """
    Update dashboard configuration.

    Admin endpoint for modifying dashboard settings.
    """
    try:
        config = DashboardConfig.get_config()
        data = request.data

        # Update configuration fields
        if "rotation_interval_seconds" in data:
            config.rotation_interval_seconds = max(1, int(data["rotation_interval_seconds"]))

        if "auto_refresh_seconds" in data:
            config.auto_refresh_seconds = max(5, int(data["auto_refresh_seconds"]))

        if "is_maintenance_mode" in data:
            config.is_maintenance_mode = bool(data["is_maintenance_mode"])

        if "maintenance_message" in data:
            config.maintenance_message = str(data["maintenance_message"])

        if "custom_css" in data:
            config.custom_css = str(data["custom_css"])

        config.save()

        return Response(
            {
                "message": "Configuration updated successfully",
                "config": {
                    "rotation_interval_seconds": config.rotation_interval_seconds,
                    "auto_refresh_seconds": config.auto_refresh_seconds,
                    "is_maintenance_mode": config.is_maintenance_mode,
                    "maintenance_message": config.maintenance_message,
                    "updated_at": config.updated_at.isoformat(),
                },
            }
        )

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def add_dashboard_message(request):
    """
    Add a new dashboard message.

    Admin endpoint for adding messages to rotation.
    """
    try:
        data = request.data

        if "message" not in data:
            return Response(
                {"error": "Message text is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        message = DashboardMessage.objects.create(
            message=data["message"],
            is_active=data.get("is_active", True),
            order=data.get("order", 0),
        )

        return Response(
            {
                "message": "Dashboard message added successfully",
                "id": message.id,
                "text": message.message,
            },
            status=status.HTTP_201_CREATED,
        )

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["GET"])
@permission_classes([AllowAny])
def get_inventory_summary(request):
    """
    Get inventory summary for dashboard display.

    Public endpoint showing overview of inventory status.
    """
    try:
        from datetime import timedelta

        from django.db.models import Count, F
        from django.utils import timezone

        from inventory.models import Asset, InventoryItem

        # Inventory Items Stats
        items_query = InventoryItem.objects.filter(is_active=True)

        total_items = items_query.count()
        low_stock_items = items_query.filter(current_stock__lte=F("minimum_stock")).count()

        # Items with pending reorders
        items_with_reorders = (
            items_query.filter(reorder_requests__status__in=["pending", "approved", "ordered"])
            .distinct()
            .count()
        )

        # Total inventory value
        total_value = sum(item.total_value for item in items_query)

        # Recently added items (last 30 days)
        thirty_days_ago = timezone.now() - timedelta(days=30)
        recent_items = items_query.filter(created_at__gte=thirty_days_ago).count()

        # Get actual low stock items list (limit to 20)
        low_stock_list = (
            items_query.filter(current_stock__lte=F("minimum_stock"))
            .order_by("current_stock")[:20]
            .values("id", "name", "current_stock", "minimum_stock", "reorder_quantity")
        )

        # Asset Stats
        assets_query = Asset.objects.filter(is_active=True)
        total_assets = assets_query.count()
        assets_by_status = dict(
            assets_query.values("status").annotate(count=Count("id")).values_list("status", "count")
        )
        assets_needing_maintenance = assets_query.filter(status="maintenance").count()

        return Response(
            {
                "inventory": {
                    "total_items": total_items,
                    "low_stock_count": low_stock_items,
                    "items_with_pending_reorders": items_with_reorders,
                    "total_value": float(total_value),
                    "recently_added": recent_items,
                    "low_stock_items": list(low_stock_list),
                },
                "assets": {
                    "total_assets": total_assets,
                    "by_status": assets_by_status,
                    "needing_maintenance": assets_needing_maintenance,
                },
                "timestamp": timezone.now().isoformat(),
            }
        )

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# Simple health check endpoint
def dashboard_health(request):
    """Simple health check for dashboard system."""
    try:
        config = DashboardConfig.get_config()
        message_count = DashboardMessage.objects.filter(is_active=True).count()

        return JsonResponse(
            {
                "status": "healthy",
                "active_messages": message_count,
                "maintenance_mode": config.is_maintenance_mode,
                "last_config_update": config.updated_at.isoformat(),
            }
        )
    except Exception as e:
        return JsonResponse({"status": "error", "error": str(e)}, status=500)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_user_widgets(request):
    """
    Get user's dashboard widget layout.

    Returns all widgets for the authenticated user, creating defaults if none exist.
    """
    try:
        user = request.user
        widgets = DashboardWidget.objects.filter(user=user, is_visible=True).order_by(
            "order", "position_y", "position_x"
        )

        # If user has no widgets, create defaults
        if not widgets.exists():
            DashboardWidget.create_default_widgets(user)
            widgets = DashboardWidget.objects.filter(user=user, is_visible=True).order_by(
                "order", "position_y", "position_x"
            )

        serializer = DashboardWidgetSerializer(widgets, many=True)
        return Response(serializer.data)

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def save_user_widgets(request):
    """
    Save/update user's dashboard widget layout.

    Accepts a list of widget configurations and updates or creates them.
    """
    try:
        user = request.user
        widgets_data = request.data.get("widgets", [])

        if not isinstance(widgets_data, list):
            return Response(
                {"error": "widgets must be a list"}, status=status.HTTP_400_BAD_REQUEST
            )

        updated_widgets = []
        for widget_data in widgets_data:
            widget_id = widget_data.get("id")
            widget_type = widget_data.get("widget_type")

            if not widget_type:
                continue

            if widget_id:
                # Update existing widget
                try:
                    widget = DashboardWidget.objects.get(id=widget_id, user=user)
                    serializer = DashboardWidgetSerializer(
                        widget, data=widget_data, partial=True
                    )
                    if serializer.is_valid():
                        serializer.save()
                        updated_widgets.append(serializer.data)
                except DashboardWidget.DoesNotExist:
                    continue
            else:
                # Create new widget
                widget_data["user"] = user.id
                serializer = DashboardWidgetSerializer(data=widget_data)
                if serializer.is_valid():
                    widget = serializer.save(user=user)
                    updated_widgets.append(serializer.data)

        return Response({"widgets": updated_widgets})

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_low_stock_data(request):
    """
    Get low stock items data for widget.

    Returns items where current_stock <= minimum_stock.
    """
    try:
        from django.db.models import F
        from django.utils import timezone

        from inventory.models import InventoryItem

        items_query = InventoryItem.objects.filter(
            is_active=True, current_stock__lte=F("minimum_stock")
        ).select_related("category", "location")

        # Apply SIG filtering if user is not staff/logistics
        user = request.user
        if user.is_authenticated and not (user.is_superuser or user.is_staff):
            from membership.utils import get_user_managed_sigs, is_logistics_member

            if not is_logistics_member(user):
                user_sigs = get_user_managed_sigs(user)
                if user_sigs.exists():
                    items_query = items_query.filter(owning_group__in=user_sigs)
                else:
                    # Regular users see space-owned items only
                    items_query = items_query.filter(owning_group__isnull=True)

        items = items_query.order_by("current_stock")[:50].values(
            "id",
            "name",
            "current_stock",
            "minimum_stock",
            "reorder_quantity",
            "category__name",
            "location__name",
        )

        return Response(
            {
                "count": items_query.count(),
                "items": list(items),
                "timestamp": timezone.now().isoformat(),
            }
        )

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_pending_reorders_data(request):
    """
    Get pending reorder requests data for widget.

    Returns reorder requests with status='pending'.
    """
    try:
        from django.utils import timezone

        from reorder_queue.models import ReorderRequest

        pending = ReorderRequest.objects.filter(status="pending").select_related(
            "item", "item__category", "item__location", "reviewed_by"
        )

        # Apply SIG filtering
        user = request.user
        if user.is_authenticated and not (user.is_superuser or user.is_staff):
            from membership.utils import get_user_managed_sigs, is_logistics_member

            if not is_logistics_member(user):
                user_sigs = get_user_managed_sigs(user)
                if user_sigs.exists():
                    pending = pending.filter(item__owning_group__in=user_sigs)
                else:
                    pending = pending.filter(item__owning_group__isnull=True)

        pending = pending.order_by("-priority", "requested_at")[:50]

        data = []
        for req in pending:
            data.append(
                {
                    "id": req.id,
                    "item_id": str(req.item.id),
                    "item_name": req.item.name,
                    "quantity": req.quantity,
                    "priority": req.priority,
                    "requested_by": req.requested_by,
                    "requested_at": req.requested_at.isoformat(),
                    "category": req.item.category.name if req.item.category else None,
                }
            )

        return Response(
            {
                "count": pending.count(),
                "requests": data,
                "timestamp": timezone.now().isoformat(),
            }
        )

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_asset_problems_data(request):
    """
    Get asset problems data for widget.

    Returns asset problems with status in ('reported', 'in_progress').
    """
    try:
        from django.utils import timezone

        from inventory.models import AssetProblem

        problems = AssetProblem.objects.filter(
            status__in=["reported", "in_progress"]
        ).select_related("asset", "part")

        # Apply SIG filtering
        user = request.user
        if user.is_authenticated and not (user.is_superuser or user.is_staff):
            from membership.utils import get_user_managed_sigs, is_logistics_member

            if not is_logistics_member(user):
                user_sigs = get_user_managed_sigs(user)
                if user_sigs.exists():
                    problems = problems.filter(asset__owning_group__in=user_sigs)
                else:
                    problems = problems.filter(asset__owning_group__isnull=True)

        problems = problems.order_by("-created_at")[:50]

        data = []
        for problem in problems:
            data.append(
                {
                    "id": str(problem.id),
                    "asset_id": str(problem.asset.id),
                    "asset_name": problem.asset.name,
                    "asset_tag": problem.asset.asset_tag,
                    "status": problem.status,
                    "reported_by": problem.reported_by,
                    "description": problem.description[:200],  # Truncate for widget
                    "created_at": problem.created_at.isoformat(),
                }
            )

        return Response(
            {
                "count": problems.count(),
                "problems": data,
                "timestamp": timezone.now().isoformat(),
            }
        )

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_qr_scans_data(request):
    """
    Get recent QR scan activity data for widget.

    Returns assets and inventory items scanned in the last 30 days, aggregated by date.
    """
    try:
        from collections import defaultdict
        from datetime import timedelta

        from django.db.models import Count, Q, TruncDate
        from django.utils import timezone

        from inventory.models import Asset, InventoryItem

        thirty_days_ago = timezone.now() - timedelta(days=30)

        # Get asset scans
        asset_scans = (
            Asset.objects.filter(
                last_scanned_at__gte=thirty_days_ago, last_scanned_at__isnull=False
            )
            .annotate(scan_date=TruncDate("last_scanned_at"))
            .values("scan_date")
            .annotate(count=Count("id"))
            .order_by("scan_date")
        )

        # Get inventory item scans
        item_scans = (
            InventoryItem.objects.filter(
                last_scanned_at__gte=thirty_days_ago, last_scanned_at__isnull=False
            )
            .annotate(scan_date=TruncDate("last_scanned_at"))
            .values("scan_date")
            .annotate(count=Count("id"))
            .order_by("scan_date")
        )

        # Aggregate by date
        scan_data = defaultdict(lambda: {"assets": 0, "items": 0, "total": 0})
        for scan in asset_scans:
            date_str = scan["scan_date"].isoformat()
            scan_data[date_str]["assets"] = scan["count"]
            scan_data[date_str]["total"] += scan["count"]

        for scan in item_scans:
            date_str = scan["scan_date"].isoformat()
            scan_data[date_str]["items"] = scan["count"]
            scan_data[date_str]["total"] += scan["count"]

        # Convert to list format
        daily_scans = [
            {"date": date, **data} for date, data in sorted(scan_data.items())
        ]

        # Get total counts
        total_asset_scans = Asset.objects.filter(
            last_scanned_at__gte=thirty_days_ago, last_scanned_at__isnull=False
        ).count()
        total_item_scans = InventoryItem.objects.filter(
            last_scanned_at__gte=thirty_days_ago, last_scanned_at__isnull=False
        ).count()

        return Response(
            {
                "total_scans": total_asset_scans + total_item_scans,
                "asset_scans": total_asset_scans,
                "item_scans": total_item_scans,
                "daily_scans": daily_scans[-30:],  # Last 30 days
                "timestamp": timezone.now().isoformat(),
            }
        )

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_deliveries_data(request):
    """
    Get recent deliveries data for widget.

    Returns deliveries from the last 30 days with delivery items.
    """
    try:
        from datetime import timedelta

        from django.utils import timezone

        from reorder_queue.models import OrderDelivery

        thirty_days_ago = timezone.now() - timedelta(days=30)

        deliveries = (
            OrderDelivery.objects.filter(delivery_date__gte=thirty_days_ago)
            .select_related("purchase_order__supplier", "received_by")
            .prefetch_related("items__purchase_order_item__item")
            .order_by("-delivery_date")[:50]
        )

        data = []
        for delivery in deliveries:
            items_count = delivery.items.count()
            total_quantity = sum(item.quantity_received for item in delivery.items.all())

            data.append(
                {
                    "id": delivery.id,
                    "delivery_date": delivery.delivery_date.isoformat(),
                    "supplier_name": delivery.purchase_order.supplier.name
                    if delivery.purchase_order.supplier
                    else None,
                    "received_by": delivery.received_by.username
                    if delivery.received_by
                    else None,
                    "items_count": items_count,
                    "total_quantity": total_quantity,
                    "purchase_order_id": delivery.purchase_order.id,
                }
            )

        return Response(
            {
                "count": deliveries.count(),
                "deliveries": data,
                "timestamp": timezone.now().isoformat(),
            }
        )

    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
