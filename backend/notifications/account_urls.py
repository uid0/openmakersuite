"""URL configuration for the account device-management API (FP3, oms-ltqs3).

Mounted at ``/api/account/`` so the endpoints resolve to
``/api/account/devices/`` (list / retrieve / forget) and
``/api/account/devices/revoke-all/`` (the "this wasn't me" action).
"""

from rest_framework.routers import DefaultRouter

from .account_views import KnownDeviceViewSet

router = DefaultRouter()
router.register(r"devices", KnownDeviceViewSet, basename="account-device")

urlpatterns = router.urls
