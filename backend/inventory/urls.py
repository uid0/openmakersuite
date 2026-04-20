"""
URL configuration for inventory app.
"""

from django.urls import include, path

from rest_framework.routers import DefaultRouter

from .views import (
    AssetPartViewSet,
    AssetReportViewSet,
    AssetViewSet,
    CategoryViewSet,
    FixtureRefillRequestViewSet,
    FixtureViewSet,
    InventoryItemViewSet,
    InventoryReportViewSet,
    ItemSupplierViewSet,
    LocationViewSet,
    MaintenanceItemViewSet,
    MaintenanceLogViewSet,
    MaintenanceMaterialViewSet,
    MaintenanceTaskViewSet,
    PriceHistoryViewSet,
    SupplierViewSet,
    UsageLogViewSet,
    WorkOrderViewSet,
    lookup_by_code,
    postmark_inbound_work_order,
)

router = DefaultRouter()
router.register(r"suppliers", SupplierViewSet)
router.register(r"categories", CategoryViewSet)
router.register(r"locations", LocationViewSet)
router.register(r"items", InventoryItemViewSet)
router.register(r"assets", AssetViewSet)
router.register(r"asset-parts", AssetPartViewSet)
router.register(r"usage-logs", UsageLogViewSet)
router.register(r"item-suppliers", ItemSupplierViewSet)
router.register(r"price-history", PriceHistoryViewSet)
router.register(r"fixtures", FixtureViewSet)
router.register(r"fixture-refill-requests", FixtureRefillRequestViewSet)
router.register(r"maintenance-items", MaintenanceItemViewSet)
router.register(r"maintenance-materials", MaintenanceMaterialViewSet)
router.register(r"maintenance-logs", MaintenanceLogViewSet)
router.register(r"maintenance-tasks", MaintenanceTaskViewSet)
router.register(r"work-orders", WorkOrderViewSet)
router.register(r"reports/inventory", InventoryReportViewSet, basename="inventory-reports")
router.register(r"reports/assets", AssetReportViewSet, basename="asset-reports")

urlpatterns = [
    path("", include(router.urls)),
    path("lookup-code/", lookup_by_code, name="lookup-by-code"),
    path(
        "webhooks/postmark-inbound-work-order/",
        postmark_inbound_work_order,
        name="postmark-inbound-work-order",
    ),
]
