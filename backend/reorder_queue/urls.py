"""
URL configuration for reorder queue app.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (AnalyticsViewSet, OrderReceiptViewSet,
                    PurchaseOrderViewSet, ReorderRequestViewSet)

router = DefaultRouter()
router.register(r"requests", ReorderRequestViewSet)
router.register(r"purchase-orders", PurchaseOrderViewSet)
router.register(r"receipts", OrderReceiptViewSet)
router.register(r"analytics", AnalyticsViewSet, basename="analytics")

urlpatterns = [
    path("", include(router.urls)),
]
