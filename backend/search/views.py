"""
Views for search functionality.
"""

from django.db.models import Q

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from inventory.models import Asset, InventoryItem, Location, Supplier
from reorder_queue.models import PurchaseOrder

from .models import RecentSearch
from .serializers import (
    RecentSearchCreateSerializer,
    RecentSearchSerializer,
    SearchResultSerializer,
)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def global_search(request):
    """
    Global search endpoint that searches across all major entity types.

    Member-only: cross-app results include inventory SKUs, asset serials,
    and supplier names that the public-facing matrix excludes.

    Query parameters:
    - q: Search query string
    - limit: Maximum results per type (default: 10)
    """
    query = request.query_params.get("q", "").strip()
    try:
        limit = int(request.query_params.get("limit", 10))
    except (ValueError, TypeError):
        limit = 10

    if not query:
        return Response({"results": []})

    results = []

    # Search Inventory Items
    inventory_items = (
        InventoryItem.objects.filter(
            Q(name__icontains=query) | Q(description__icontains=query) | Q(sku__icontains=query)
        )
        .select_related("category", "location")
        .order_by("name")[:limit]
    )
    for item in inventory_items:
        results.append(
            {
                "id": str(item.id),
                "type": "inventory",
                "title": item.name,
                "subtitle": (
                    f"SKU: {item.sku}"
                    if item.sku
                    else item.category.name if item.category else None
                ),
                "url": f"/inventory/items/{item.id}",
            }
        )

    # Search Assets
    assets = (
        Asset.objects.filter(
            Q(name__icontains=query)
            | Q(description__icontains=query)
            | Q(serial_number__icontains=query)
            | Q(asset_tag__icontains=query)
            | Q(manufacturer_name__icontains=query)
        )
        .select_related("category", "location", "manufacturer")
        .order_by("name")[:limit]
    )
    for asset in assets:
        subtitle_parts = []
        if asset.asset_tag:
            subtitle_parts.append(f"Tag: {asset.asset_tag}")
        if asset.location:
            subtitle_parts.append(asset.location.name)
        results.append(
            {
                "id": str(asset.id),
                "type": "asset",
                "title": asset.name,
                "subtitle": " | ".join(subtitle_parts) if subtitle_parts else None,
                "url": f"/assets/{asset.id}",
            }
        )

    # Search Purchase Orders
    purchase_orders = (
        PurchaseOrder.objects.filter(
            Q(po_number__icontains=query)
            | Q(supplier__name__icontains=query)
            | Q(notes__icontains=query)
        )
        .select_related("supplier")
        .order_by("-order_date")[:limit]
    )
    for po in purchase_orders:
        results.append(
            {
                "id": str(po.id),
                "type": "purchase_order",
                "title": f"PO #{po.po_number or po.id}",
                "subtitle": f"{po.supplier.name} - {po.get_status_display()}",
                "url": f"/purchasing/orders/{po.id}",
            }
        )

    # Search Suppliers
    suppliers = Supplier.objects.filter(
        Q(name__icontains=query) | Q(notes__icontains=query)
    ).order_by("name")[:limit]
    for supplier in suppliers:
        results.append(
            {
                "id": str(supplier.id),
                "type": "supplier",
                "title": supplier.name,
                "subtitle": supplier.get_supplier_type_display(),
                "url": f"/inventory/suppliers/{supplier.id}",
            }
        )

    # Search Locations
    locations = (
        Location.objects.filter(Q(name__icontains=query) | Q(description__icontains=query))
        .select_related("parent")
        .order_by("name")[:limit]
    )
    for location in locations:
        results.append(
            {
                "id": str(location.id),
                "type": "location",
                "title": location.name,
                "subtitle": location.description[:100] if location.description else None,
                "url": f"/inventory/locations/{location.id}",
            }
        )

    serializer = SearchResultSerializer(results, many=True)
    return Response({"results": serializer.data})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_recent_searches(request):
    """
    Get user's recent searches.

    Query parameters:
    - limit: Maximum number of results (default: 20)
    """
    try:
        limit = int(request.query_params.get("limit", 20))
    except (ValueError, TypeError):
        limit = 20
    searches = RecentSearch.objects.filter(user=request.user).order_by("-searched_at")[:limit]
    serializer = RecentSearchSerializer(searches, many=True)
    return Response({"results": serializer.data})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def save_recent_search(request):
    """
    Save a recent search for the authenticated user.
    """
    serializer = RecentSearchCreateSerializer(data=request.data)
    if serializer.is_valid():
        # Only save if user is authenticated
        recent_search = serializer.save(user=request.user)

        # Limit to last 50 searches per user (delete older ones)
        user_searches = RecentSearch.objects.filter(user=request.user).order_by("-searched_at")
        if user_searches.count() > 50:
            # Get IDs of searches to delete (all except the first 50)
            ids_to_delete = list(user_searches.values_list("id", flat=True)[50:])
            RecentSearch.objects.filter(id__in=ids_to_delete).delete()

        result_serializer = RecentSearchSerializer(recent_search)
        return Response(result_serializer.data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
