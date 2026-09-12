"""
URL configuration for reorder queue app.
"""

from django.urls import include, path

from config.routers import ApiRouter

from .views import (
    AnalyticsViewSet,
    OrderReceiptViewSet,
    PurchaseOrderViewSet,
    PurchasingReportViewSet,
    ReorderRequestViewSet,
    WebHookViewSet,
)

router = ApiRouter()
router.register(r"requests", ReorderRequestViewSet)
router.register(r"purchase-orders", PurchaseOrderViewSet)
router.register(r"receipts", OrderReceiptViewSet)
router.register(r"analytics", AnalyticsViewSet, basename="analytics")
router.register(r"reports/purchasing", PurchasingReportViewSet, basename="purchasing-reports")
router.register(r"webhooks", WebHookViewSet)

urlpatterns = [
    path("", include(router.urls)),
]
