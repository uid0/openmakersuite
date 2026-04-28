"""URL configuration for the third-party maintenance work order API."""

from django.urls import include, path

from rest_framework.routers import DefaultRouter

from .views import (
    AssetWarrantyViewSet,
    EmergencyAuthorizationViewSet,
    ThirdPartyWorkOrderAssetViewSet,
    ThirdPartyWorkOrderAttachmentViewSet,
    ThirdPartyWorkOrderQuoteViewSet,
    ThirdPartyWorkOrderViewSet,
    asset_wo_status,
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
router.register(r"quotes", ThirdPartyWorkOrderQuoteViewSet, basename="third-party-work-order-quote")
router.register(
    r"emergency-authorizations",
    EmergencyAuthorizationViewSet,
    basename="emergency-authorization",
)
router.register(r"warranties", AssetWarrantyViewSet, basename="asset-warranty")

urlpatterns = [
    path("", include(router.urls)),
    path(
        "assets/<uuid:asset_id>/wo-status/",
        asset_wo_status,
        name="third-party-wo-asset-status",
    ),
]
