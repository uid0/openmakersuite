"""
URL configuration for inventory app.
"""

from django.urls import include, path

from rest_framework.routers import DefaultRouter

from .views import CategoryViewSet, InventoryItemViewSet, SupplierViewSet, UsageLogViewSet

router = DefaultRouter()
router.register(r"suppliers", SupplierViewSet)
router.register(r"categories", CategoryViewSet)
router.register(r"items", InventoryItemViewSet)
router.register(r"usage-logs", UsageLogViewSet)

urlpatterns = [
    path("", include(router.urls)),
]
