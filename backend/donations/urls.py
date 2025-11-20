"""
URL configuration for donations app.
"""

from django.urls import include, path

from rest_framework.routers import DefaultRouter

from .views import (
    DispositionViewSet,
    DonationItemViewSet,
    DonationViewSet,
    lookup_donation_item_by_code,
)

router = DefaultRouter()
router.register(r"donations", DonationViewSet, basename="donation")
router.register(r"donation-items", DonationItemViewSet, basename="donation-item")
router.register(r"dispositions", DispositionViewSet, basename="disposition")

urlpatterns = [
    path("lookup-code/", lookup_donation_item_by_code, name="lookup-donation-item-by-code"),
    path("", include(router.urls)),
]
