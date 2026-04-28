"""
Views for ForgeKey API.
"""

import json
import logging

from django.conf import settings
from django.utils import timezone

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    AssetAuthorization,
    AssetDevice,
    DeviceFirmwareUpdate,
    DeviceLockout,
    DeviceType,
    DeviceUsage,
    ESP32Device,
    ESP32DevicePhoto,
    FirmwareVersion,
    LockoutLevel,
    OperationalMode,
    PowerMeterReading,
)
from .serializers import (
    AssetAuthorizationSerializer,
    AssetDeviceSerializer,
    DeviceFirmwareUpdateSerializer,
    DeviceLockoutSerializer,
    DeviceTypeSerializer,
    DeviceUsageSerializer,
    ESP32DeviceSerializer,
    FirmwareVersionSerializer,
    OperationalModeSerializer,
    PowerMeterReadingSerializer,
)
from .tasks import disable_device, enable_device, request_device_status
from .utils import (
    generate_device_jwt,
    get_mqtt_firmware_topic,
    get_mqtt_ping_topic,
    normalize_mac_address,
    normalize_sensor_kind,
    verify_device_jwt,
)

logger = logging.getLogger(__name__)


JPEG_MAGIC = b"\xff\xd8\xff"
PLACEHOLDER_PROVISIONING_TOKEN = "REPLACE_ME_PROVISIONING_TOKEN"  # nosec B105


def _provisioning_token_valid(request) -> bool:
    expected = getattr(settings, "FORGEKEY_PROVISIONING_TOKEN", "")
    if not expected or expected == PLACEHOLDER_PROVISIONING_TOKEN:
        return False
    supplied = request.headers.get("x-forgekey-provisioning-token", "")
    if not supplied or supplied == PLACEHOLDER_PROVISIONING_TOKEN:
        return False
    return supplied == expected


class ForgeKeyDeviceRegisterView(APIView):
    """
    POST /api/forgekey/devices/register/

    Accepts a multipart/form-data request from a freshly-booted ESP32 device
    containing an enrollment photo plus identifying metadata. Idempotent on
    ``mac_address`` — re-posting updates the existing row.

    Auth: shared FORGEKEY_PROVISIONING_TOKEN, supplied via the
    ``X-ForgeKey-Provisioning-Token`` request header.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        if not _provisioning_token_valid(request):
            return Response(
                {"detail": "Invalid or missing provisioning token."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Metadata may arrive either as top-level multipart fields or as a
        # JSON blob under the "metadata" field — accept both shapes.
        meta_blob = request.data.get("metadata")
        if isinstance(meta_blob, str):
            try:
                meta = json.loads(meta_blob)
            except json.JSONDecodeError:
                return Response(
                    {"detail": "metadata field must be valid JSON."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            meta = {k: v for k, v in request.data.items() if k not in ("photo", "metadata")}

        mac_raw = meta.get("mac_address")
        if not mac_raw:
            return Response(
                {"detail": "mac_address is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            mac = normalize_mac_address(mac_raw)
        except Exception:
            return Response(
                {"detail": "mac_address is malformed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw_device_type = meta.get("device_type") or meta.get("sensor_kind")
        device_type_code = normalize_sensor_kind(raw_device_type) if raw_device_type else None
        device_type_obj = None
        if device_type_code:
            device_type_obj = DeviceType.objects.filter(code=device_type_code).first()
            if device_type_obj is None:
                return Response(
                    {"detail": f"Unknown device_type '{raw_device_type}'."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        defaults = {
            "firmware_version": meta.get("firmware_version", ""),
            "boot_count": meta.get("boot_count"),
            "free_heap": meta.get("free_heap"),
            "ip": meta.get("ip"),
            "last_seen": timezone.now(),
        }
        if device_type_obj is not None:
            defaults["device_type"] = device_type_obj

        device = ESP32Device.objects.filter(mac_address=mac).first()
        created = device is None
        if created:
            if device_type_obj is None:
                return Response(
                    {"detail": "device_type is required for first registration."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            device = ESP32Device(mac_address=mac, **defaults)
        else:
            for field, value in defaults.items():
                if value is not None and value != "":
                    setattr(device, field, value)

        photo = request.FILES.get("photo")
        if photo is not None:
            device.enrollment_photo = photo

        device.save()

        token = generate_device_jwt(mac)
        sensor_kind_for_topic = device_type_code or (
            device.device_type.code if device.device_type_id else ""
        )
        return Response(
            {
                "device_id": str(device.id),
                "assigned_location_id": (str(device.location_id) if device.location_id else None),
                "mqtt_topic_for_firmware": get_mqtt_firmware_topic(mac),
                "mqtt_topic_for_pings": get_mqtt_ping_topic(mac, sensor_kind_for_topic),
                "jwt_token": token,
                "created": created,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class ForgeKeyDevicePhotoUploadView(APIView):
    """
    POST /api/forgekey/devices/<mac>/photo/

    Accepts a JPEG photo upload from an enrolled device. Auth via the JWT
    issued at registration; falls back to the provisioning token so a device
    can re-enroll if its JWT has aged out.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, mac):
        try:
            normalized_mac = normalize_mac_address(mac)
        except Exception:
            return Response(
                {"detail": "mac is malformed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        device = ESP32Device.objects.filter(mac_address=normalized_mac).first()
        if device is None:
            return Response(
                {"detail": "Unknown device."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not self._authorize(request, normalized_mac):
            return Response(
                {"detail": "Authentication failed."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        photo = request.FILES.get("photo")
        if photo is None:
            return Response(
                {"detail": "photo is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        head = photo.read(3)
        photo.seek(0)
        if head != JPEG_MAGIC:
            return Response(
                {"detail": "photo must be a JPEG image."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        captured_at = request.data.get("captured_at") or None
        record = ESP32DevicePhoto.objects.create(
            device=device,
            image=photo,
            captured_at=captured_at,
        )

        device.last_photo = record.image
        device.last_seen = timezone.now()
        device.is_online = True
        device.save(update_fields=["last_photo", "last_seen", "is_online", "updated_at"])

        return Response(
            {
                "photo_id": str(record.id),
                "received_at": record.received_at.isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )

    @staticmethod
    def _authorize(request, mac: str) -> bool:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()
            payload = verify_device_jwt(token, mac)
            if payload and payload.get("mac") == mac:
                return True
        return _provisioning_token_valid(request)


class DeviceTypeViewSet(viewsets.ModelViewSet):
    """API endpoint for device types."""

    queryset = DeviceType.objects.all()
    serializer_class = DeviceTypeSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]


class ESP32DeviceViewSet(viewsets.ModelViewSet):
    """API endpoint for ESP32 devices."""

    queryset = ESP32Device.objects.select_related("device_type").all()
    serializer_class = ESP32DeviceSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    @action(detail=True, methods=["post"])
    def enable(self, request, pk=None):
        """Enable a device (turn on power, etc.)."""
        device = self.get_object()
        enable_device.delay(device.mac_address)
        return Response({"status": "enable command sent", "device": device.mac_address})

    @action(detail=True, methods=["post"])
    def disable(self, request, pk=None):
        """Disable a device (turn off power, etc.)."""
        device = self.get_object()
        delay_seconds = int(request.data.get("delay_seconds", 0))
        disable_device.delay(device.mac_address, delay_seconds=delay_seconds)
        return Response(
            {
                "status": "disable command sent",
                "device": device.mac_address,
                "delay": delay_seconds,
            }
        )

    @action(detail=True, methods=["post"])
    def status(self, request, pk=None):
        """Request status from a device."""
        device = self.get_object()
        request_device_status.delay(device.mac_address)
        return Response({"status": "status request sent", "device": device.mac_address})


class AssetDeviceViewSet(viewsets.ModelViewSet):
    """API endpoint for asset-device relationships."""

    queryset = AssetDevice.objects.select_related("asset", "device", "device__device_type").all()
    serializer_class = AssetDeviceSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]


class OperationalModeViewSet(viewsets.ModelViewSet):
    """API endpoint for operational modes."""

    queryset = OperationalMode.objects.select_related("asset", "classroom_mode_enabled_by").all()
    serializer_class = OperationalModeSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    @action(detail=True, methods=["post"])
    def enable_classroom_mode(self, request, pk=None):
        """Enable classroom mode for an asset."""
        mode = self.get_object()
        user = request.user

        if not user.is_authenticated:
            return Response(
                {"error": "Authentication required"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        mode.classroom_mode_enabled = True
        mode.classroom_mode_enabled_by = user
        mode.classroom_mode_enabled_at = timezone.now()
        mode.mode = OperationalMode.MODE_CLASSROOM
        mode.save()

        serializer = self.get_serializer(mode)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def disable_classroom_mode(self, request, pk=None):
        """Disable classroom mode for an asset."""
        mode = self.get_object()
        user = request.user

        if not user.is_authenticated:
            return Response(
                {"error": "Authentication required"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        mode.classroom_mode_enabled = False
        mode.classroom_mode_enabled_by = None
        mode.classroom_mode_enabled_at = None
        if mode.mode == OperationalMode.MODE_CLASSROOM:
            mode.mode = OperationalMode.MODE_AVAILABLE
        mode.save()

        serializer = self.get_serializer(mode)
        return Response(serializer.data)


class AssetAuthorizationViewSet(viewsets.ModelViewSet):
    """API endpoint for asset authorizations."""

    queryset = AssetAuthorization.objects.select_related("asset", "user", "authorized_by").all()
    serializer_class = AssetAuthorizationSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def perform_create(self, serializer):
        """Set authorized_by to current user when creating authorization."""
        serializer.save(authorized_by=self.request.user)

    @action(detail=False, methods=["post"])
    def add_via_classroom_mode(self, request):
        """Add user to asset authorization list via classroom mode QR code scan."""
        asset_id = request.data.get("asset_id")
        user = request.user

        if not user.is_authenticated:
            return Response(
                {"error": "Authentication required"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        try:
            from inventory.models import Asset

            asset = Asset.objects.get(id=asset_id)
        except Asset.DoesNotExist:
            return Response({"error": "Asset not found"}, status=status.HTTP_404_NOT_FOUND)

        # Check if classroom mode is enabled
        try:
            mode = OperationalMode.objects.get(asset=asset)
            if not mode.classroom_mode_enabled:
                return Response(
                    {"error": "Classroom mode is not enabled for this asset"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except OperationalMode.DoesNotExist:
            return Response(
                {"error": "Classroom mode is not enabled for this asset"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Create or update authorization
        authorization, created = AssetAuthorization.objects.get_or_create(
            asset=asset,
            user=user,
            defaults={"authorized_by": user, "is_active": True},
        )

        if not created:
            authorization.is_active = True
            authorization.save()

        serializer = self.get_serializer(authorization)
        return Response(
            serializer.data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class DeviceLockoutViewSet(viewsets.ModelViewSet):
    """API endpoint for device lockouts."""

    queryset = DeviceLockout.objects.select_related("asset", "locked_by", "unlocked_by").all()
    serializer_class = DeviceLockoutSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def perform_create(self, serializer):
        """Create a lockout and determine the lockout level."""
        user = self.request.user
        asset = serializer.validated_data["asset"]

        # Determine lockout level based on user's permissions
        lockout_level = self._get_user_lockout_level(user, asset)

        # Update operational mode
        mode, _ = OperationalMode.objects.get_or_create(asset=asset)
        mode.mode = OperationalMode.MODE_LOCKED_OUT
        mode.save()

        serializer.save(locked_by=user, lockout_level=lockout_level)

    @action(detail=True, methods=["post"])
    def unlock(self, request, pk=None):
        """Unlock a device based on hierarchical permissions."""
        lockout = self.get_object()
        user = request.user

        if not user.is_authenticated:
            return Response(
                {"error": "Authentication required"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not lockout.can_be_unlocked_by(user):
            return Response(
                {"error": "You do not have permission to unlock this device"},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Check if there are other active lockouts
        other_lockouts = DeviceLockout.objects.filter(asset=lockout.asset, is_active=True).exclude(
            id=lockout.id
        )

        # Mark this lockout as inactive
        lockout.is_active = False
        lockout.unlocked_at = timezone.now()
        lockout.unlocked_by = user
        lockout.save()

        # If no other active lockouts, update operational mode
        if not other_lockouts.exists():
            try:
                mode = OperationalMode.objects.get(asset=lockout.asset)
                if mode.mode == OperationalMode.MODE_LOCKED_OUT:
                    mode.mode = OperationalMode.MODE_AVAILABLE
                    mode.save()
            except OperationalMode.DoesNotExist:
                pass

        serializer = self.get_serializer(lockout)
        return Response(serializer.data)

    def _get_user_lockout_level(self, user, asset):
        """Determine the lockout level for a user."""
        # Check for COO
        from django.contrib.auth.models import Group

        try:
            coo_group = Group.objects.get(name="COO")
            if coo_group in user.groups.all() or user.is_superuser:
                return LockoutLevel.COO
        except Group.DoesNotExist:
            if user.is_superuser:
                return LockoutLevel.COO

        # Check for Logistics Lead
        try:
            logistics_lead_group = Group.objects.get(name="Logistics Lead")
            if logistics_lead_group in user.groups.all():
                return LockoutLevel.LOGISTICS_LEAD
        except Group.DoesNotExist:
            pass

        # Check for Logistics Team
        try:
            logistics_group = Group.objects.get(name="Logistics")
            if logistics_group in user.groups.all():
                return LockoutLevel.LOGISTICS_TEAM
        except Group.DoesNotExist:
            pass

        # Check for Group Admin
        if asset.owning_group and asset.owning_group in user.groups.all():
            if (
                user.has_perm("inventory.group_admin")
                or user.groups.filter(name__endswith="_admin").exists()
            ):
                return LockoutLevel.GROUP_ADMIN

        # Check for Maintainer
        try:
            maintainer_group = Group.objects.get(name="Maintainer")
            if maintainer_group in user.groups.all():
                return LockoutLevel.MAINTAINER
        except Group.DoesNotExist:
            pass

        # Default to user level
        return LockoutLevel.USER


class DeviceUsageViewSet(viewsets.ModelViewSet):
    """API endpoint for device usage sessions."""

    queryset = DeviceUsage.objects.select_related("asset", "user").all()
    serializer_class = DeviceUsageSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def perform_create(self, serializer):
        """Set user to current user when creating usage session."""
        serializer.save(user=self.request.user)

    @action(detail=True, methods=["post"])
    def end_session(self, request, pk=None):
        """End a usage session."""
        usage = self.get_object()
        usage.end_session()
        serializer = self.get_serializer(usage)
        return Response(serializer.data)


class PowerMeterReadingViewSet(viewsets.ReadOnlyModelViewSet):
    """API endpoint for power meter readings (read-only)."""

    queryset = PowerMeterReading.objects.select_related("device", "asset", "usage_session").all()
    serializer_class = PowerMeterReadingSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]


class FirmwareVersionViewSet(viewsets.ModelViewSet):
    """API endpoint for firmware versions."""

    queryset = FirmwareVersion.objects.select_related("device_type", "created_by").all()
    serializer_class = FirmwareVersionSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        """Set created_by to current user when creating firmware version."""
        serializer.save(created_by=self.request.user)


class DeviceFirmwareUpdateViewSet(viewsets.ModelViewSet):
    """API endpoint for firmware updates."""

    queryset = DeviceFirmwareUpdate.objects.select_related(
        "device", "firmware_version", "requested_by"
    ).all()
    serializer_class = DeviceFirmwareUpdateSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        """Set requested_by to current user and start the update process."""
        serializer.save(requested_by=self.request.user)
        # TODO: Trigger firmware update via MQTT
        # This would send the firmware file and signature to the device
