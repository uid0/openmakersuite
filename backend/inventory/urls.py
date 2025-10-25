"""
URL configuration for inventory app.
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import SupplierViewSet, CategoryViewSet, InventoryItemViewSet, UsageLogViewSet

router = DefaultRouter()
router.register(r"suppliers", SupplierViewSet)
router.register(r"categories", CategoryViewSet)
router.register(r"items", InventoryItemViewSet)
router.register(r"usage-logs", UsageLogViewSet)

urlpatterns = [
    path("", include(router.urls)),
]
