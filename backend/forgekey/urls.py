"""
URL routing for ForgeKey API.
"""

from django.urls import include, path

from rest_framework.routers import DefaultRouter

from .views import (
    AssetAuthorizationViewSet,
    AssetDeviceViewSet,
    CertificateAuthorityViewSet,
    DeviceCertificateViewSet,
    DeviceFirmwareUpdateViewSet,
    DeviceLockoutViewSet,
    DeviceTypeViewSet,
    DeviceUsageViewSet,
    EPaperDisplayBatteryView,
    EPaperDisplayBindView,
    EPaperDisplayCommandAckView,
    EPaperDisplayDesiredView,
    EPaperDisplayFirmwareStatusView,
    EPaperDisplayHealthView,
    EPaperDisplayImageView,
    EPaperDisplayListView,
    EPaperDisplaySetActiveView,
    EPaperDisplaySetRotationView,
    EPaperFirmwareCheckView,
    EpaperFirmwareRolloutViewSet,
    EPaperServiceCompleteView,
    EPaperServiceInfoView,
    ESP32DeviceViewSet,
    FirmwareBuildViewSet,
    FirmwareRolloutViewSet,
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
router.register(r"firmware-rollouts", FirmwareRolloutViewSet, basename="firmware-rollout")
router.register(
    r"epaper-firmware-rollouts",
    EpaperFirmwareRolloutViewSet,
    basename="epaper-firmware-rollout",
)
router.register(r"firmware-builds", FirmwareBuildViewSet, basename="firmware-build")
router.register(
    r"certificate-authorities", CertificateAuthorityViewSet, basename="certificate-authority"
)
router.register(r"device-certificates", DeviceCertificateViewSet, basename="device-certificate")

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
        "epaper/",
        EPaperDisplayListView.as_view(),
        name="epaper-list",
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
        "epaper/<uuid:display_id>/health/",
        EPaperDisplayHealthView.as_view(),
        name="epaper-health",
    ),
    path(
        "epaper/<uuid:display_id>/desired.json",
        EPaperDisplayDesiredView.as_view(),
        name="epaper-desired",
    ),
    path(
        "epaper/<uuid:display_id>/command/status/",
        EPaperDisplayCommandAckView.as_view(),
        name="epaper-command-status",
    ),
    path(
        "epaper/<uuid:display_id>/firmware/status/",
        EPaperDisplayFirmwareStatusView.as_view(),
        name="epaper-firmware-status",
    ),
    path(
        "epaper/<uuid:display_id>/firmware-check/",
        EPaperFirmwareCheckView.as_view(),
        name="epaper-firmware-check",
    ),
    path(
        "epaper/<uuid:display_id>/bind/",
        EPaperDisplayBindView.as_view(),
        name="epaper-bind",
    ),
    path(
        "epaper/<uuid:display_id>/set-active/",
        EPaperDisplaySetActiveView.as_view(),
        name="epaper-set-active",
    ),
    path(
        "epaper/<uuid:display_id>/set-rotation/",
        EPaperDisplaySetRotationView.as_view(),
        name="epaper-set-rotation",
    ),
    path(
        "epaper/<uuid:display_id>/service-info/",
        EPaperServiceInfoView.as_view(),
        name="epaper-service-info",
    ),
    path(
        "epaper/<uuid:display_id>/complete/",
        EPaperServiceCompleteView.as_view(),
        name="epaper-complete",
    ),
    path("", include(router.urls)),
]
