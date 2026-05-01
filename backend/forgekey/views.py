"""
Views for ForgeKey API.
"""

import hashlib
import hmac
import json
import logging
import re

from django.conf import settings
from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.utils import timezone

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import (
    AllowAny,
    IsAdminUser,
    IsAuthenticated,
    IsAuthenticatedOrReadOnly,
)
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
    OccupancyEvent,
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
    OccupancyEventSerializer,
    OperationalModeSerializer,
    PowerMeterReadingSerializer,
)
from .services.device_commands import DeviceCommandError, publish_command
from .services.firmware_download_token import verify_download_token
from .services.firmware_signing import (
    FirmwareSigningError,
    get_public_key_pem,
    is_signing_configured,
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

# Stable error codes returned to the device so firmware can log a meaningful
# diagnostic instead of a generic 401. Safe to expose: they identify the
# failure mode without revealing any part of the configured secret.
AUTH_ERR_SERVER_UNCONFIGURED = "server_unconfigured"
AUTH_ERR_TOKEN_MISSING = "token_missing"  # nosec B105 — error code, not a password
AUTH_ERR_TOKEN_PLACEHOLDER = "token_placeholder"  # nosec B105 — error code, not a password
AUTH_ERR_TOKEN_MISMATCH = "token_mismatch"  # nosec B105 — error code, not a password


def _token_fingerprint(token: str) -> str:
    """Short, non-reversible fingerprint of a token for operator diagnosis.

    Returns the first 8 hex chars of SHA-256(token). High-entropy tokens are
    not recoverable from this prefix, so it is safe to log/return; operators
    can compare fingerprints across the device, the OMS env, and the rotate
    command without exposing the secret itself.
    """
    if not token:
        return ""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]


def _classify_provisioning_token(request) -> tuple[bool, str | None, dict]:
    """Validate the provisioning token and classify any failure.

    Returns ``(ok, error_code, diagnostics)``. ``diagnostics`` contains
    non-secret fields suitable for the response body and server logs:
    ``expected_fp``, ``supplied_fp``, ``expected_len``, ``supplied_len``.
    """
    raw_expected = getattr(settings, "FORGEKEY_PROVISIONING_TOKEN", "") or ""
    expected = raw_expected.strip()
    raw_supplied = request.headers.get("x-forgekey-provisioning-token", "") or ""
    supplied = raw_supplied.strip()

    diagnostics = {
        "expected_fp": _token_fingerprint(expected),
        "supplied_fp": _token_fingerprint(supplied),
        "expected_len": len(expected),
        "supplied_len": len(supplied),
    }

    if not expected or expected == PLACEHOLDER_PROVISIONING_TOKEN:
        return False, AUTH_ERR_SERVER_UNCONFIGURED, diagnostics
    if not supplied:
        return False, AUTH_ERR_TOKEN_MISSING, diagnostics
    if supplied == PLACEHOLDER_PROVISIONING_TOKEN:
        return False, AUTH_ERR_TOKEN_PLACEHOLDER, diagnostics
    if not hmac.compare_digest(supplied, expected):
        return False, AUTH_ERR_TOKEN_MISMATCH, diagnostics
    return True, None, diagnostics


# Human-readable hints paired with each error code. Kept short so they fit
# inside ESP32 serial logs.
_AUTH_ERR_DETAILS = {
    AUTH_ERR_SERVER_UNCONFIGURED: (
        "Server has no provisioning token configured. "
        "Set FORGEKEY_PROVISIONING_TOKEN on the backend."
    ),
    AUTH_ERR_TOKEN_MISSING: ("Request is missing the X-ForgeKey-Provisioning-Token header."),
    AUTH_ERR_TOKEN_PLACEHOLDER: (
        "Device sent the placeholder provisioning token; "
        "publish the real token via rotate_provisioning_token."
    ),
    AUTH_ERR_TOKEN_MISMATCH: (
        "Provisioning token does not match the server's configured value. "
        "Compare token fingerprints to identify the drift."
    ),
}


def _provisioning_auth_error_response(error_code: str, diagnostics: dict) -> Response:
    """Build a 401 response that distinguishes the failure mode."""
    payload = {
        "detail": _AUTH_ERR_DETAILS.get(error_code, "Provisioning auth failed."),
        "code": error_code,
        "expected_token_fingerprint": diagnostics["expected_fp"],
        "supplied_token_fingerprint": diagnostics["supplied_fp"],
        "expected_token_length": diagnostics["expected_len"],
        "supplied_token_length": diagnostics["supplied_len"],
    }
    return Response(payload, status=status.HTTP_401_UNAUTHORIZED)


def _provisioning_token_valid(request) -> bool:
    ok, _err, _diag = _classify_provisioning_token(request)
    return ok


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
        ok, error_code, diagnostics = _classify_provisioning_token(request)
        if not ok:
            logger.warning(
                "ForgeKey register: provisioning auth failed code=%s "
                "expected_fp=%s expected_len=%d supplied_fp=%s supplied_len=%d "
                "remote=%s",
                error_code,
                diagnostics["expected_fp"] or "<empty>",
                diagnostics["expected_len"],
                diagnostics["supplied_fp"] or "<empty>",
                diagnostics["supplied_len"],
                request.META.get("REMOTE_ADDR", "?"),
            )
            return _provisioning_auth_error_response(error_code, diagnostics)

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
                valid_codes = sorted(
                    DeviceType.objects.filter(is_active=True).values_list("code", flat=True)
                )
                logger.warning(
                    "ForgeKey register: unknown device_type %r (normalized=%r) from mac=%s",
                    raw_device_type,
                    device_type_code,
                    mac,
                )
                return Response(
                    {
                        "detail": (
                            f"Unknown device_type '{raw_device_type}'. "
                            f"Valid: {', '.join(valid_codes)}"
                        ),
                        "valid_device_types": valid_codes,
                    },
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
                valid_codes = sorted(
                    DeviceType.objects.filter(is_active=True).values_list("code", flat=True)
                )
                return Response(
                    {
                        "detail": (
                            "device_type is required for first registration. "
                            f"Valid: {', '.join(valid_codes)}"
                        ),
                        "valid_device_types": valid_codes,
                    },
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

    @action(
        detail=True,
        methods=["post"],
        url_path="command/restart",
        permission_classes=[IsAdminUser],
    )
    def command_restart(self, request, pk=None):
        """Tell the device to reboot."""
        device = self.get_object()
        return self._dispatch_command(device, request.user, {"cmd": "restart"}, "restart")

    @action(
        detail=True,
        methods=["post"],
        url_path="command/capture-photo",
        permission_classes=[IsAdminUser],
    )
    def command_capture_photo(self, request, pk=None):
        """Tell the device to capture and upload a single photo."""
        device = self.get_object()
        payload: dict = {"cmd": "capture"}
        upload_url = request.data.get("upload_url")
        if upload_url:
            payload["upload_url"] = upload_url
        return self._dispatch_command(device, request.user, payload, "capture_photo")

    @action(
        detail=True,
        methods=["post"],
        url_path="command/blink",
        permission_classes=[IsAdminUser],
    )
    def command_blink(self, request, pk=None):
        """Blink the device's onboard indicator."""
        device = self.get_object()
        payload: dict = {"cmd": "blink"}
        pattern = request.data.get("pattern")
        if pattern:
            payload["pattern"] = pattern
        try:
            duration_s = request.data.get("duration_s")
            if duration_s is not None:
                payload["duration_s"] = int(duration_s)
        except (TypeError, ValueError):
            return Response(
                {"detail": "duration_s must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return self._dispatch_command(device, request.user, payload, "blink")

    @action(
        detail=True,
        methods=["post"],
        url_path="command/firmware-update",
        permission_classes=[IsAdminUser],
    )
    def command_firmware_update(self, request, pk=None):
        """Trigger an OTA firmware update.

        Accepts either a ``firmware_version_id`` (preferred — reuses the
        existing dispatch service so a ``DeviceFirmwareUpdate`` audit row is
        recorded) or an explicit ``version`` + ``url`` pair for ad-hoc
        rollbacks.
        """
        device = self.get_object()
        firmware_version_id = request.data.get("firmware_version_id")
        if firmware_version_id:
            try:
                firmware = FirmwareVersion.objects.get(id=firmware_version_id)
            except FirmwareVersion.DoesNotExist:
                return Response(
                    {"detail": "firmware_version_id does not exist."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            from .services.firmware_dispatch import publish_firmware_update

            try:
                records = publish_firmware_update(device, firmware, requested_by=request.user)
            except RuntimeError as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
            logger.info(
                "forgekey.audit firmware_update device=%s mac=%s firmware=%s actor=%s",
                device.id,
                device.mac_address,
                firmware.version,
                getattr(request.user, "username", None),
            )
            return Response(
                {
                    "status": "firmware_update dispatched",
                    "device": device.mac_address,
                    "firmware_version": firmware.version,
                    "update_id": str(records[0].id) if records else None,
                }
            )

        version = request.data.get("version")
        url = request.data.get("url")
        if not version or not url:
            return Response(
                {
                    "detail": (
                        "firmware_version_id is required, or supply both "
                        "'version' and 'url' for an ad-hoc dispatch."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        payload = {"cmd": "ota", "version": version, "url": url}
        return self._dispatch_command(device, request.user, payload, "firmware_update")

    @action(detail=True, methods=["get"], url_path="occupancy")
    def occupancy(self, request, pk=None):
        """Return recent occupancy events for charting in the UI."""
        device = self.get_object()
        since = request.query_params.get("since", "24h")
        try:
            cutoff = _parse_since_window(since)
        except ValueError:
            return Response(
                {"detail": "since must be like '24h', '7d', or an ISO timestamp."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        events_qs = OccupancyEvent.objects.filter(
            device=device,
            event_timestamp_utc__gte=cutoff,
        ).order_by("event_timestamp_utc")
        # Cap to a reasonable upper bound so a chatty device can't OOM the
        # frontend; the chart only needs a few hundred points.
        events = list(events_qs[:1000])

        return Response(
            {
                "device": device.mac_address,
                "since": cutoff.isoformat(),
                "current_occupancy": OccupancyEvent.current_occupancy_for(device),
                "events": OccupancyEventSerializer(events, many=True).data,
            }
        )

    def _dispatch_command(self, device, actor, payload, audit_action):
        try:
            topic = publish_command(device, payload, actor=actor, audit_action=audit_action)
        except DeviceCommandError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(
            {
                "status": f"{audit_action} command sent",
                "device": device.mac_address,
                "topic": topic,
                "dispatched_at": payload.get("timestamp", timezone.now().isoformat()),
            }
        )


def _parse_since_window(raw: str):
    """Parse a ``since`` query param into a UTC cutoff timestamp.

    Supports the ``Nh`` / ``Nd`` / ``Nm`` shorthands and ISO-8601 strings.
    Raises ``ValueError`` on anything else.
    """
    from datetime import datetime, timedelta

    text = (raw or "").strip()
    if not text:
        return timezone.now() - timedelta(hours=24)
    if text[-1] in {"h", "H"}:
        return timezone.now() - timedelta(hours=int(text[:-1]))
    if text[-1] in {"d", "D"}:
        return timezone.now() - timedelta(days=int(text[:-1]))
    if text[-1] in {"m", "M"}:
        return timezone.now() - timedelta(minutes=int(text[:-1]))
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"unparseable since: {raw!r}") from exc
    if parsed.tzinfo is None:
        from datetime import timezone as dt_tz

        parsed = parsed.replace(tzinfo=dt_tz.utc)
    return parsed


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


_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def _parse_range(header: str, size: int):
    """Parse a single-range ``Range`` header. Returns ``(start, end)`` or ``None``.

    ``end`` is inclusive. We accept the common forms:
      * ``bytes=START-END``  (both bounds)
      * ``bytes=START-``     (open-ended; up to size-1)
      * ``bytes=-SUFFIX``    (last SUFFIX bytes)
    Multi-range requests are not supported; returning ``None`` causes the
    caller to fall back to a full 200 response.
    """
    if not header:
        return None
    m = _RANGE_RE.match(header.strip())
    if not m:
        return None
    start_s, end_s = m.group(1), m.group(2)
    if start_s == "" and end_s == "":
        return None
    if start_s == "":
        suffix = int(end_s)
        if suffix <= 0:
            return None
        start = max(0, size - suffix)
        end = size - 1
    else:
        start = int(start_s)
        end = int(end_s) if end_s else size - 1
    if start >= size or end < start:
        return None
    end = min(end, size - 1)
    return start, end


def _stream_file_chunk(file_obj, start: int, length: int, *, chunk_size: int = 64 * 1024):
    file_obj.seek(start)
    remaining = length
    while remaining > 0:
        read_size = min(chunk_size, remaining)
        data = file_obj.read(read_size)
        if not data:
            break
        remaining -= len(data)
        yield data


class ForgeKeyFirmwareDownloadView(APIView):
    """
    GET /api/forgekey/firmware/<id>/download

    Authorized either by:
      * a short-lived HMAC token (``token`` + ``exp`` query params) issued
        by ``forgekey.services.firmware_download_token.make_download_token``
        and embedded in the MQTT trigger payload, or
      * a device JWT in the ``Authorization: Bearer <token>`` header
        (``mac`` query param identifies the device for verification).

    Supports the HTTP ``Range`` header so an ESP32 can resume an interrupted
    download. Multi-range requests are not supported — the response falls
    back to a full 200 in that case.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request, firmware_id):
        firmware = FirmwareVersion.objects.filter(pk=firmware_id, is_active=True).first()
        if firmware is None:
            return Response(
                {"detail": "Firmware not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not self._authorize(request, firmware_id):
            return Response(
                {"detail": "Invalid or expired firmware download token."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not firmware.firmware_file:
            return Response(
                {"detail": "Firmware binary is missing."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            size = firmware.firmware_file.size
        except OSError:
            return Response(
                {"detail": "Firmware binary is missing."},
                status=status.HTTP_404_NOT_FOUND,
            )

        range_header = request.headers.get("range") or request.META.get("HTTP_RANGE", "")
        rng = _parse_range(range_header, size)

        download_name = (
            firmware.firmware_file.name.rsplit("/", 1)[-1]
            if firmware.firmware_file.name
            else f"firmware-{firmware.version}.bin"
        )
        content_type = "application/octet-stream"

        if rng is None:
            # Full content. Use FileResponse so we get a streaming body.
            handle = firmware.firmware_file.open("rb")
            response = FileResponse(handle, content_type=content_type)
            response["Content-Length"] = str(size)
            response["Accept-Ranges"] = "bytes"
            response["Content-Disposition"] = f'attachment; filename="{download_name}"'
            return response

        start, end = rng
        length = end - start + 1
        handle = firmware.firmware_file.open("rb")
        response = StreamingHttpResponse(
            _stream_file_chunk(handle, start, length),
            status=status.HTTP_206_PARTIAL_CONTENT,
            content_type=content_type,
        )
        response["Content-Length"] = str(length)
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
        response["Accept-Ranges"] = "bytes"
        response["Content-Disposition"] = f'attachment; filename="{download_name}"'
        return response

    @staticmethod
    def _authorize(request, firmware_id: str) -> bool:
        token = request.query_params.get("token") or request.GET.get("token")
        exp = request.query_params.get("exp") or request.GET.get("exp")
        if token and exp:
            try:
                exp_int = int(exp)
            except (TypeError, ValueError):
                return False
            if verify_download_token(str(firmware_id), token, exp_int):
                return True

        # Fallback: device JWT (Authorization: Bearer <jwt>) tied to a MAC
        # supplied via ``?mac=`` so we can derive the device-specific secret.
        auth = request.headers.get("authorization", "")
        mac = request.query_params.get("mac") or request.GET.get("mac") or ""
        if auth.lower().startswith("bearer ") and mac:
            jwt_token = auth.split(" ", 1)[1].strip()
            try:
                normalized = normalize_mac_address(mac)
            except Exception:
                return False
            payload = verify_device_jwt(jwt_token, normalized)
            if payload and payload.get("mac") == normalized:
                return True
        return False


class ForgeKeyFirmwarePublicKeyView(APIView):
    """
    GET /api/forgekey/firmware/public-key

    Returns the active ECDSA(P-256) public key in PEM form. Used by:
      * Firmware build scripts that bake the verifying key into the image.
      * Operator tooling that wants to verify a firmware artifact offline.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request):
        if not is_signing_configured():
            return Response(
                {"detail": "Firmware signing is not configured."},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            pem = get_public_key_pem()
        except FirmwareSigningError as exc:
            logger.warning("Cannot return firmware public key: %s", exc)
            return Response(
                {"detail": "Firmware signing key is misconfigured."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return HttpResponse(pem, content_type="application/x-pem-file")
