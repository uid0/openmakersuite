"""
URL configuration for donations app.
"""

from django.urls import include, path

from rest_framework.routers import DefaultRouter

from .views import (
    DispositionViewSet,
    DonationItemViewSet,
    DonationViewSet,
    TaxReceiptViewSet,
    lookup_donation_item_by_code,
    lookup_tax_receipt,
    upload_signature,
)

router = DefaultRouter()
router.register(r"donations", DonationViewSet, basename="donation")
router.register(r"donation-items", DonationItemViewSet, basename="donation-item")
router.register(r"dispositions", DispositionViewSet, basename="disposition")
router.register(r"tax-receipts", TaxReceiptViewSet, basename="tax-receipt")

urlpatterns = [
    path(
        "lookup-code/",
        lookup_donation_item_by_code,
        name="lookup-donation-item-by-code",
    ),
    path("tax-receipts/lookup/", lookup_tax_receipt, name="lookup-tax-receipt"),
    path("upload-signature/", upload_signature, name="upload-signature"),
    path("", include(router.urls)),
]
