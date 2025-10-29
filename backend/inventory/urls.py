"""
URL configuration for inventory app.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    CategoryViewSet,
    InventoryItemViewSet,
    ItemSupplierViewSet,
    PriceHistoryViewSet,
    SupplierViewSet,
    UsageLogViewSet,
)

router = DefaultRouter()
router.register(r"suppliers", SupplierViewSet)
router.register(r"categories", CategoryViewSet)
router.register(r"items", InventoryItemViewSet)
router.register(r"usage-logs", UsageLogViewSet)
router.register(r"item-suppliers", ItemSupplierViewSet)
router.register(r"price-history", PriceHistoryViewSet)

urlpatterns = [
    path("", include(router.urls)),
]
