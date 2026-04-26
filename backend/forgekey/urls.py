"""
URL routing for ForgeKey API.
"""

from django.urls import include, path

from rest_framework.routers import DefaultRouter

from .views import (
    AssetAuthorizationViewSet,
    AssetDeviceViewSet,
    DeviceFirmwareUpdateViewSet,
    DeviceLockoutViewSet,
    DeviceTypeViewSet,
    DeviceUsageViewSet,
    ESP32DeviceViewSet,
    FirmwareVersionViewSet,
    ForgeKeyDevicePhotoUploadView,
    ForgeKeyDeviceRegisterView,
    OperationalModeViewSet,
    PowerMeterReadingViewSet,
)

router = DefaultRouter()
router.register(r"device-types", DeviceTypeViewSet, basename="device-type")
router.register(r"devices", ESP32DeviceViewSet, basename="esp32-device")
router.register(r"asset-devices", AssetDeviceViewSet, basename="asset-device")
router.register(r"operational-modes", OperationalModeViewSet, basename="operational-mode")
router.register(r"authorizations", AssetAuthorizationViewSet, basename="asset-authorization")
router.register(r"lockouts", DeviceLockoutViewSet, basename="device-lockout")
router.register(r"usage", DeviceUsageViewSet, basename="device-usage")
router.register(r"power-readings", PowerMeterReadingViewSet, basename="power-reading")
router.register(r"firmware-versions", FirmwareVersionViewSet, basename="firmware-version")
router.register(r"firmware-updates", DeviceFirmwareUpdateViewSet, basename="firmware-update")

app_name = "forgekey"

urlpatterns = [
    path(
        "devices/register/",
        ForgeKeyDeviceRegisterView.as_view(),
        name="device-register",
    ),
    path(
        "devices/<str:mac>/photo/",
        ForgeKeyDevicePhotoUploadView.as_view(),
        name="device-photo-upload",
    ),
    path("", include(router.urls)),
]
