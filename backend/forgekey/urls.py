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
    EPaperDisplayBatteryView,
    EPaperDisplayBindView,
    EPaperDisplayImageView,
    ESP32DeviceViewSet,
    FirmwareVersionViewSet,
    ForgeKeyCertificateRevocationListView,
    ForgeKeyDeviceEnrollView,
    ForgeKeyDevicePhotoUploadView,
    ForgeKeyFirmwareDownloadView,
    ForgeKeyFirmwarePublicKeyView,
    ForgeKeyJWKSView,
    ForgeKeyOmsCommandPublicKeyView,
    MqttWebhookView,
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
        "devices/enroll/",
        ForgeKeyDeviceEnrollView.as_view(),
        name="device-enroll",
    ),
    path(
        "devices/<str:mac>/photo/",
        ForgeKeyDevicePhotoUploadView.as_view(),
        name="device-photo-upload",
    ),
    path(
        "firmware/public-key",
        ForgeKeyFirmwarePublicKeyView.as_view(),
        name="firmware-public-key",
    ),
    path(
        "oms-command-public-key.pem",
        ForgeKeyOmsCommandPublicKeyView.as_view(),
        name="oms-command-public-key",
    ),
    path(
        "ca/crl.pem",
        ForgeKeyCertificateRevocationListView.as_view(),
        name="ca-crl",
    ),
    path(
        "jwks/",
        ForgeKeyJWKSView.as_view(),
        name="device-jwks",
    ),
    path(
        "firmware/<uuid:firmware_id>/download",
        ForgeKeyFirmwareDownloadView.as_view(),
        name="firmware-download",
    ),
    path(
        "mqtt-webhook/",
        MqttWebhookView.as_view(),
        name="mqtt-webhook",
    ),
    path(
        "epaper/<uuid:display_id>/image.png",
        EPaperDisplayImageView.as_view(),
        name="epaper-image",
    ),
    path(
        "epaper/<uuid:display_id>/battery/",
        EPaperDisplayBatteryView.as_view(),
        name="epaper-battery",
    ),
    path(
        "epaper/<uuid:display_id>/bind/",
        EPaperDisplayBindView.as_view(),
        name="epaper-bind",
    ),
    path("", include(router.urls)),
]
