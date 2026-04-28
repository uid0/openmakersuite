"""URL configuration for the third-party maintenance work order API."""

from django.urls import include, path

from rest_framework.routers import DefaultRouter

from .views import (
    ThirdPartyWorkOrderAssetViewSet,
    ThirdPartyWorkOrderAttachmentViewSet,
    ThirdPartyWorkOrderViewSet,
)

router = DefaultRouter()
router.register(r"work-orders", ThirdPartyWorkOrderViewSet, basename="third-party-work-order")
router.register(
    r"asset-links",
    ThirdPartyWorkOrderAssetViewSet,
    basename="third-party-work-order-asset",
)
router.register(
    r"attachments",
    ThirdPartyWorkOrderAttachmentViewSet,
    basename="third-party-work-order-attachment",
)

urlpatterns = [
    path("", include(router.urls)),
]
