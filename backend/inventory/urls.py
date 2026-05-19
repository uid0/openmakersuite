"""
URL configuration for inventory app.
"""

from django.urls import include, path

from rest_framework.routers import DefaultRouter

from .safety_sheet import LocationSafetySheetView
from .views import (
    AssetPartViewSet,
    AssetProblemViewSet,
    AssetReportViewSet,
    AssetViewSet,
    CategoryViewSet,
    FixtureRefillRequestViewSet,
    FixtureViewSet,
    InventoryItemViewSet,
    InventoryReconciliationViewSet,
    InventoryReportViewSet,
    ItemSupplierViewSet,
    LocationProblemViewSet,
    LocationViewSet,
    MaintenanceDashboardViewSet,
    MaintenanceItemViewSet,
    MaintenanceLogViewSet,
    MaintenanceMaterialViewSet,
    MaintenanceRecordViewSet,
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
router.register(r"asset-problems", AssetProblemViewSet)
router.register(r"location-problems", LocationProblemViewSet)
router.register(r"asset-parts", AssetPartViewSet)
router.register(r"usage-logs", UsageLogViewSet)
router.register(r"item-suppliers", ItemSupplierViewSet)
router.register(r"price-history", PriceHistoryViewSet)
router.register(r"fixtures", FixtureViewSet)
router.register(r"fixture-refill-requests", FixtureRefillRequestViewSet)
router.register(r"maintenance", MaintenanceDashboardViewSet, basename="maintenance")
router.register(r"maintenance-items", MaintenanceItemViewSet)
router.register(r"maintenance-materials", MaintenanceMaterialViewSet)
router.register(r"maintenance-logs", MaintenanceLogViewSet)
router.register(r"maintenance-records", MaintenanceRecordViewSet, basename="maintenance-record")
router.register(r"maintenance-tasks", MaintenanceTaskViewSet)
router.register(r"work-orders", WorkOrderViewSet)
router.register(r"reports/inventory", InventoryReportViewSet, basename="inventory-reports")
router.register(r"reports/assets", AssetReportViewSet, basename="asset-reports")
router.register(
    r"reconciliations",
    InventoryReconciliationViewSet,
    basename="inventory-reconciliations",
)

urlpatterns = [
    path(
        "locations/<int:location_id>/safety-sheet/",
        LocationSafetySheetView.as_view(),
        name="inventory-location-safety-sheet",
    ),
    path(
        "locations/<int:location_id>/reconcile/",
        InventoryReconciliationViewSet.as_view({"get": "location_grid"}),
        name="inventory-location-reconcile-grid",
    ),
    path(
        "locations/<int:location_id>/reconcile/export/",
        InventoryReconciliationViewSet.as_view({"get": "location_export"}),
        name="inventory-location-reconcile-export",
    ),
    path("", include(router.urls)),
    path("lookup-code/", lookup_by_code, name="lookup-by-code"),
    path(
        "webhooks/postmark-inbound-work-order/",
        postmark_inbound_work_order,
        name="postmark-inbound-work-order",
    ),
]
