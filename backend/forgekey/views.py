"""
Views for ForgeKey API.
"""

import hashlib
import hmac
import json
import logging
import re
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.utils import timezone

import sentry_sdk
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
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

from .audit import record_event as record_audit_event
from .models import (
    AssetAuthorization,
    AssetDevice,
    CertificateAuthority,
    DeviceCertificate,
    DeviceCommand,
    DeviceEnrollment,
    DeviceFirmwareUpdate,
    DeviceIdentity,
    DeviceLockout,
    DeviceType,
    DeviceUsage,
    EPaperDisplay,
    EpaperFirmwareRollout,
    ESP32Device,
    ESP32DevicePhoto,
    FirmwareBuild,
    FirmwareRollout,
    FirmwareVersion,
    ForgeKeyAuditEvent,
    IndicatorBinding,
    LockoutLevel,
    OccupancyEvent,
    OperationalMode,
    PowerMeterReading,
    RoomOperationalMode,
    TemperatureReading,
)
from .serializers import (
    AssetAuthorizationSerializer,
    AssetDeviceSerializer,
    CertificateAuthoritySerializer,
    DeviceCertificateSerializer,
    DeviceCommandSerializer,
    DeviceFirmwareUpdateSerializer,
    DeviceLockoutSerializer,
    DeviceTypeSerializer,
    DeviceUsageSerializer,
    EPaperDisplaySerializer,
    EpaperFirmwareRolloutSerializer,
    ESP32DeviceSerializer,
    FirmwareBuildSerializer,
    FirmwareRolloutSerializer,
    FirmwareVersionSerializer,
    IndicatorBindingSerializer,
    OccupancyEventSerializer,
    OperationalModeSerializer,
    PowerMeterReadingSerializer,
    RoomOperationalModeSerializer,
    TemperatureReadingSerializer,
)
from .services.ca_key_storage import CaKeyStorageError, decrypt_ca_key
from .services.csr_signing import CsrSigningError, CsrValidationError, sign_csr
from .services.device_commands import DeviceCommandError, publish_command
from .services.firmware_download_token import verify_download_token
from .services.firmware_signing import (
    FirmwareSigningError,
    get_public_key_pem,
    is_signing_configured,
)
from .services.indicator import send_indicator_test, sync_indicator
from .services.jwt_signing import (
    JwtSigningError,
    get_jwt_jwks,
    get_jwt_public_key_pem,
    is_jwt_signing_configured,
)
from .services.mtls_auth import verify_mtls_request
from .tasks import (
    disable_device,
    enable_device,
    process_mqtt_device_capabilities,
    process_mqtt_firmware_update_response,
    process_mqtt_occupancy,
    process_mqtt_power_reading,
    process_mqtt_reading,
    process_mqtt_status_message,
    request_device_status,
)
from .utils import (
    device_command_topic_for,
    device_firmware_topic_for,
    device_ping_topic_for,
    device_status_topic_for,
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


class ForgeKeyDeviceEnrollView(APIView):
    """
    POST /api/forgekey/devices/enroll/

    Bootstrap endpoint for ESP32 devices. The firmware posts a multipart
    request containing:

      * ``metadata`` (JSON): ``mac_address``, ``firmware_version``,
        ``sensor_kind``, ``csr_pem``, ``unique_chip_id``, ``chip_info``,
        plus optional ``boot_count``, ``free_heap``, ``ip``,
        ``flash_memory_id``.
      * ``photo`` (image/jpeg, optional — non-imaging sensors send
        metadata-only).

    On success the server creates / refreshes the per-chip ``DeviceIdentity``,
    issues a fresh mTLS client certificate signed by the active CA, revokes
    the prior certificate if one existed, binds the legacy ``ESP32Device`` row
    to the identity, and returns the certificate, the OMS command-verification
    public key, and a broker policy tailored to this device.

    Auth: shared ``FORGEKEY_PROVISIONING_TOKEN`` supplied in the
    ``X-ForgeKey-Provisioning-Token`` header (same bootstrap secret used by
    the legacy ``/register/`` endpoint).
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        ok, error_code, diagnostics = _classify_provisioning_token(request)
        if not ok:
            logger.warning(
                "ForgeKey enroll: provisioning auth failed code=%s "
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

        meta_blob = request.data.get("metadata")
        if isinstance(meta_blob, str):
            try:
                meta = json.loads(meta_blob)
            except json.JSONDecodeError:
                return Response(
                    {
                        "detail": "metadata field must be valid JSON.",
                        "code": "metadata_invalid_json",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
        elif isinstance(meta_blob, dict):
            meta = meta_blob
        else:
            meta = {k: v for k, v in request.data.items() if k not in ("photo", "metadata")}

        unique_chip_id = (meta.get("unique_chip_id") or "").strip()
        if not unique_chip_id:
            return Response(
                {
                    "detail": "unique_chip_id is required.",
                    "code": "unique_chip_id_missing",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        csr_pem = meta.get("csr_pem") or ""
        if not csr_pem:
            return Response(
                {"detail": "csr_pem is required.", "code": "csr_pem_missing"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        mac_raw = meta.get("mac_address") or ""
        mac_normalized = ""
        if mac_raw:
            try:
                mac_normalized = normalize_mac_address(mac_raw)
            except Exception:
                return Response(
                    {
                        "detail": "mac_address is malformed.",
                        "code": "mac_address_invalid",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        raw_sensor_kind = meta.get("sensor_kind") or meta.get("device_type") or ""
        sensor_kind_code = normalize_sensor_kind(raw_sensor_kind) if raw_sensor_kind else ""

        token_fp_full = (
            hashlib.sha256(
                (request.headers.get("x-forgekey-provisioning-token", "") or "").encode("utf-8")
            ).hexdigest()
            if request.headers.get("x-forgekey-provisioning-token")
            else ""
        )

        identity, _ = DeviceIdentity.objects.get_or_create(device_id=unique_chip_id)
        if identity.status == DeviceIdentity.STATUS_DECOMMISSIONED:
            logger.warning(
                "ForgeKey enroll: refusing re-issue for decommissioned chip %s",
                unique_chip_id,
            )
            return Response(
                {
                    "detail": "Device identity is decommissioned.",
                    "code": "identity_decommissioned",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            signed = sign_csr(
                csr_pem,
                device_id=unique_chip_id,
            )
        except CsrValidationError as exc:
            logger.info("ForgeKey enroll: rejecting CSR — %s", exc)
            return Response(
                {"detail": str(exc), "code": "csr_invalid"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except CsrSigningError as exc:
            logger.error("ForgeKey enroll: CA unavailable — %s", exc)
            return Response(
                {"detail": str(exc), "code": "ca_unavailable"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            command_public_pem = get_jwt_public_key_pem()
        except JwtSigningError as exc:
            logger.error("ForgeKey enroll: command-key not configured — %s", exc)
            return Response(
                {
                    "detail": "Command-signing key is not configured.",
                    "code": "command_key_unconfigured",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        now = timezone.now()
        ttl_seconds = int(getattr(settings, "FORGEKEY_ENROLLMENT_SESSION_TTL_SECONDS", 600) or 600)
        active_ca = CertificateAuthority.get_active()
        ca_name = active_ca.name if active_ca else ""

        with transaction.atomic():
            enrollment = DeviceEnrollment.objects.create(
                device=identity,
                csr_pem=csr_pem,
                unique_chip_id=unique_chip_id,
                mac_address=mac_normalized,
                sensor_kind=sensor_kind_code,
                firmware_version=meta.get("firmware_version", "") or "",
                chip_info=meta.get("chip_info") or {},
                boot_count=meta.get("boot_count"),
                free_heap=meta.get("free_heap"),
                ip_address=meta.get("ip") or meta.get("ip_address") or None,
                flash_memory_id=meta.get("flash_memory_id", "") or "",
                token_fingerprint=token_fp_full,
                status=DeviceEnrollment.STATUS_PENDING,
                expires_at=now + timedelta(seconds=ttl_seconds),
            )

            # Re-enrollment supersedes the previous cert.
            DeviceCertificate.objects.filter(device=identity, revoked_at__isnull=True).update(
                revoked_at=now
            )

            certificate = DeviceCertificate.objects.create(
                device=identity,
                serial=signed.serial,
                subject=signed.subject,
                fingerprint_sha256=signed.fingerprint_sha256,
                not_before=signed.not_before,
                not_after=signed.not_after,
                issued_by=ca_name,
            )

            enrollment.certificate = certificate
            enrollment.status = DeviceEnrollment.STATUS_ISSUED
            enrollment.approved_at = now
            photo = request.FILES.get("photo")
            if photo is not None:
                enrollment.enrollment_photo = photo
            enrollment.save()

            esp_device = None
            if mac_normalized:
                esp_device = ESP32Device.objects.filter(mac_address=mac_normalized).first()
                if esp_device is None:
                    device_type_obj = None
                    if sensor_kind_code:
                        device_type_obj = DeviceType.objects.filter(code=sensor_kind_code).first()
                    if device_type_obj is not None:
                        esp_device = ESP32Device.objects.create(
                            mac_address=mac_normalized,
                            device_type=device_type_obj,
                            firmware_version=meta.get("firmware_version", "") or "",
                            boot_count=meta.get("boot_count"),
                            free_heap=meta.get("free_heap"),
                            ip=meta.get("ip") or meta.get("ip_address"),
                            last_seen=now,
                            identity=identity,
                        )
                else:
                    update_fields = []
                    if esp_device.identity_id != identity.id:
                        esp_device.identity = identity
                        update_fields.append("identity")
                    if meta.get("firmware_version"):
                        esp_device.firmware_version = meta["firmware_version"]
                        update_fields.append("firmware_version")
                    if meta.get("boot_count") is not None:
                        esp_device.boot_count = meta["boot_count"]
                        update_fields.append("boot_count")
                    if meta.get("free_heap") is not None:
                        esp_device.free_heap = meta["free_heap"]
                        update_fields.append("free_heap")
                    raw_ip = meta.get("ip") or meta.get("ip_address")
                    if raw_ip:
                        esp_device.ip = raw_ip
                        update_fields.append("ip")
                    esp_device.last_seen = now
                    update_fields.append("last_seen")
                    if photo is not None:
                        esp_device.enrollment_photo = photo
                        update_fields.append("enrollment_photo")
                    if update_fields:
                        update_fields.append("updated_at")
                        esp_device.save(update_fields=update_fields)

        broker_host = settings.PUBLIC_MQTT_BROKER_HOST or settings.MQTT_BROKER_HOST
        broker_port = settings.PUBLIC_MQTT_BROKER_PORT
        broker_tls = settings.PUBLIC_MQTT_BROKER_USE_TLS
        firmware_topic = device_firmware_topic_for(unique_chip_id)
        ping_topic = device_ping_topic_for(unique_chip_id)
        command_topic = device_command_topic_for(unique_chip_id)
        status_topic = device_status_topic_for(unique_chip_id)

        policy = {
            "mqtt_broker_host": broker_host,
            "mqtt_broker_port": broker_port,
            "mqtt_broker_use_tls": broker_tls,
            "mqtt_topic_for_firmware": firmware_topic,
            "mqtt_topic_for_pings": ping_topic,
            "mqtt_topic_for_commands": command_topic,
            "mqtt_topic_for_status": status_topic,
        }

        body = {
            "device_id": unique_chip_id,
            "client_certificate_pem": signed.cert_pem,
            "command_public_key_pem": command_public_pem,
            "policy": policy,
            # Top-level mirror for the firmware's firstString(policy[k], resp[k])
            # fallback. Drop in a follow-up cleanup PR once firmware has settled
            # on the nested form everywhere.
            "mqtt_broker_host": broker_host,
            "mqtt_broker_port": broker_port,
            "mqtt_broker_use_tls": broker_tls,
            "mqtt_topic_for_firmware": firmware_topic,
            "mqtt_topic_for_pings": ping_topic,
        }
        return Response(body, status=status.HTTP_201_CREATED)


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


class MqttWebhookView(APIView):
    """
    POST /api/forgekey/mqtt-webhook/

    EMQX WebHook bridge target. EMQX is configured to POST every published
    MQTT message matching the configured topic filters to this endpoint as
    JSON. We authenticate the request, parse the topic + payload, and
    dispatch to the matching processor Celery task. Returns 204 once the
    task is queued so EMQX does not block on Celery completion.

    Auth (in order):
      1. If ``settings.FORGEKEY_WEBHOOK_ALLOWED_IPS`` is set, REMOTE_ADDR
         must match one of the entries.
      2. The ``X-ForgeKey-Webhook-Secret`` header must match
         ``settings.FORGEKEY_WEBHOOK_SECRET`` (constant-time compare).

    Both checks run before any payload parsing so unauthenticated spam
    cannot consume CPU on JSON decode or DB lookups.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]
    parser_classes = [JSONParser]

    OCCUPANCY_SUFFIX = "occupancy"
    READING_SUFFIX = "reading"
    STATUS_SUFFIX = "status"
    POWER_SUFFIX = "power"
    CAPABILITIES_SUFFIX = "capabilities"
    FIRMWARE_RESPONSE_SUFFIX = ("firmware", "response")

    def post(self, request):
        if not self._ip_allowed(request):
            logger.warning(
                "ForgeKey webhook: IP %s not in allowlist",
                request.META.get("REMOTE_ADDR", "?"),
            )
            return Response(
                {"detail": "Source IP not allowed."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not self._secret_valid(request):
            logger.warning(
                "ForgeKey webhook: secret mismatch from %s",
                request.META.get("REMOTE_ADDR", "?"),
            )
            return Response(
                {"detail": "Invalid webhook secret."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        body = request.data
        if not isinstance(body, dict):
            return Response(
                {"detail": "Request body must be a JSON object."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        topic = body.get("topic")
        if not isinstance(topic, str) or not topic:
            return Response(
                {"detail": "topic is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # EMQX sends payload as a JSON-encoded string by default; some bridge
        # configurations forward it as a parsed object. Accept both.
        raw_payload = body.get("payload")
        if isinstance(raw_payload, str):
            try:
                message_data = json.loads(raw_payload) if raw_payload else {}
            except json.JSONDecodeError:
                logger.warning("ForgeKey webhook: malformed JSON payload on topic %s", topic)
                return Response(
                    {"detail": "payload is not valid JSON."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        elif isinstance(raw_payload, dict) or raw_payload is None:
            message_data = raw_payload or {}
        else:
            return Response(
                {"detail": "payload must be a JSON object or string."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        parts = topic.split("/")
        # Topic shape: <prefix>/<mac-segment>/[<sensor>/]<suffix...>
        if len(parts) < 3:
            return Response(
                {"detail": "topic is too short to extract a MAC."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            mac = normalize_mac_address(parts[1])
        except Exception:
            return Response(
                {"detail": "topic MAC segment is malformed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        dispatched = self._dispatch(parts, mac, message_data)
        if not dispatched:
            logger.info("ForgeKey webhook: ignoring unrouted topic %s", topic)
            # Still 204 — EMQX should not retry topics we deliberately drop.
        return Response(status=status.HTTP_204_NO_CONTENT)

    @staticmethod
    def _ip_allowed(request) -> bool:
        allowed = getattr(settings, "FORGEKEY_WEBHOOK_ALLOWED_IPS", []) or []
        if not allowed:
            return True
        return request.META.get("REMOTE_ADDR", "") in allowed

    @staticmethod
    def _secret_valid(request) -> bool:
        expected = getattr(settings, "FORGEKEY_WEBHOOK_SECRET", "") or ""
        if not expected:
            # Fail closed: an empty configured secret means the deployment
            # forgot to set FORGEKEY_WEBHOOK_SECRET; we refuse all traffic.
            return False
        supplied = request.headers.get("x-forgekey-webhook-secret", "") or ""
        return hmac.compare_digest(supplied, expected)

    def _dispatch(self, parts: list[str], mac: str, message_data: dict) -> bool:
        """Route the parsed message; return True if a task was queued."""
        last = parts[-1]
        # Status: <prefix>/<mac>/status (3 parts, last == "status")
        if len(parts) == 3 and last == self.STATUS_SUFFIX:
            process_mqtt_status_message.delay(mac, message_data)
            return True

        # Capabilities: <prefix>/<mac>/capabilities (3 parts, last == "capabilities")
        if len(parts) == 3 and last == self.CAPABILITIES_SUFFIX:
            process_mqtt_device_capabilities.delay(mac, message_data or {})
            return True

        # Firmware response: <prefix>/<mac>/firmware/response
        if (
            len(parts) >= 4
            and parts[-2] == self.FIRMWARE_RESPONSE_SUFFIX[0]
            and parts[-1] == self.FIRMWARE_RESPONSE_SUFFIX[1]
        ):
            update_id = message_data.get("update_id") if isinstance(message_data, dict) else None
            status_value = message_data.get("status") if isinstance(message_data, dict) else None
            error_message = (
                message_data.get("error_message") if isinstance(message_data, dict) else None
            )
            if not update_id or not status_value:
                logger.warning(
                    "ForgeKey webhook: firmware response missing update_id/status for %s",
                    mac,
                )
                return False
            process_mqtt_firmware_update_response.delay(
                mac, str(update_id), str(status_value), error_message
            )
            return True

        # Occupancy: <prefix>/<mac>/<sensor>/occupancy (4 parts)
        if len(parts) == 4 and last == self.OCCUPANCY_SUFFIX:
            sensor_kind = normalize_sensor_kind(parts[2])
            process_mqtt_occupancy.delay(mac, sensor_kind, message_data or {})
            return True

        # Temperature/humidity: <prefix>/<mac>/<sensor>/reading (4 parts)
        if len(parts) == 4 and last == self.READING_SUFFIX:
            sensor_kind = normalize_sensor_kind(parts[2])
            process_mqtt_reading.delay(mac, sensor_kind, message_data or {})
            return True

        # Power: <prefix>/<mac>/power or <prefix>/<mac>/<sensor>/power
        if last == self.POWER_SUFFIX and len(parts) in (3, 4):
            asset_id = message_data.get("asset_id") if isinstance(message_data, dict) else None
            if not asset_id:
                logger.warning("ForgeKey webhook: power reading from %s missing asset_id", mac)
                return False
            process_mqtt_power_reading.delay(mac, str(asset_id), message_data)
            return True

        return False


class DeviceTypeViewSet(viewsets.ModelViewSet):
    """API endpoint for device types."""

    queryset = DeviceType.objects.all()
    serializer_class = DeviceTypeSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_permissions(self):
        # Reads stay open to authenticated users (the device + build forms need
        # the type list); managing the lookup table itself is staff-only.
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [IsAdminUser()]
        return super().get_permissions()


class ESP32DeviceViewSet(viewsets.ModelViewSet):
    """API endpoint for ESP32 devices."""

    queryset = ESP32Device.objects.select_related("device_type").all()
    serializer_class = ESP32DeviceSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        qs = super().get_queryset()
        capability = self.request.query_params.get("capability")
        if capability:
            from django.db import connection

            if connection.features.supports_json_field_contains:
                # Postgres path — index-friendly, used in production.
                qs = qs.filter(capabilities__contains=[capability])
            else:
                # SQLite fallback (tests). The capability list is small per
                # device and the device count is bounded, so a Python-side
                # filter is acceptable here.
                ids = [
                    d.id
                    for d in qs.values_list("id", "capabilities", named=True)
                    if capability in (d.capabilities or [])
                ]
                qs = qs.filter(id__in=ids)
        return qs

    def get_permissions(self):
        # Deleting a device is destructive (drops its command history) — keep
        # it staff-only even though reads/edits are open to authenticated users.
        if self.action == "destroy":
            return [IsAdminUser()]
        return super().get_permissions()

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

    @action(detail=True, methods=["post"], permission_classes=[IsAdminUser])
    def retire(self, request, pk=None):
        """Take a device out of service (``is_active=False``) — staff only.

        Keeps the row + its history (unlike delete) and is reversible via
        ``reactivate``.
        """
        device = self.get_object()
        device.is_active = False
        device.save(update_fields=["is_active", "updated_at"])
        return Response(self.get_serializer(device).data)

    @action(detail=True, methods=["post"], permission_classes=[IsAdminUser])
    def reactivate(self, request, pk=None):
        """Return a retired device to service (``is_active=True``) — staff only."""
        device = self.get_object()
        device.is_active = True
        device.save(update_fields=["is_active", "updated_at"])
        return Response(self.get_serializer(device).data)

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
        url_path="command/ping",
        permission_classes=[IsAdminUser],
    )
    def command_ping(self, request, pk=None):
        """Force a device to publish its current status payload now."""
        device = self.get_object()
        return self._dispatch_command(device, request.user, {"cmd": "status"}, "ping")

    @action(
        detail=True,
        methods=["post"],
        url_path="command/identify",
        permission_classes=[IsAdminUser],
    )
    def command_identify(self, request, pk=None):
        """Run the firmware's 'find-me' routine — extended blink with countdown.

        Distinct from ``blink`` so the audit log + history table call out
        physical-finding usage. Defaults to a 30-second pattern; callers can
        override ``duration_s``.
        """
        device = self.get_object()
        try:
            duration_raw = request.data.get("duration_s", 30)
            duration_s = int(duration_raw) if duration_raw is not None else 30
        except (TypeError, ValueError):
            return Response(
                {"detail": "duration_s must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if duration_s <= 0 or duration_s > 600:
            return Response(
                {"detail": "duration_s must be between 1 and 600."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        payload = {"cmd": "identify", "duration_s": duration_s}
        return self._dispatch_command(device, request.user, payload, "identify")

    @action(
        detail=True,
        methods=["get"],
        url_path="recent-commands",
        permission_classes=[IsAdminUser],
    )
    def recent_commands(self, request, pk=None):
        """Return the last N commands sent to this device, with ack state.

        Drives the live ack feedback + history table on the device-detail
        page. ``limit`` is capped at 100 so a chatty UI poll can't pull the
        whole audit log.
        """
        device = self.get_object()
        try:
            limit = int(request.query_params.get("limit", 10))
        except (TypeError, ValueError):
            limit = 10
        limit = max(1, min(limit, 100))
        commands = list(
            DeviceCommand.objects.filter(device=device)
            .select_related("sent_by")
            .order_by("-sent_at")[:limit]
        )
        return Response(
            {
                "device": device.mac_address,
                "results": DeviceCommandSerializer(commands, many=True).data,
            }
        )

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
        url_path="indicator/test",
        permission_classes=[IsAdminUser],
    )
    def indicator_test(self, request, pk=None):
        """Send an explicit color/brightness/pattern preview to an indicator.

        Bypasses status derivation — the admin picks the presentation directly
        for a live preview / hardware check. Validation + payload shaping live
        in the indicator service so they stay aligned with the sync path.
        """
        device = self.get_object()
        try:
            record, payload = send_indicator_test(
                device,
                color=request.data.get("color"),
                brightness=request.data.get("brightness"),
                pattern=request.data.get("pattern"),
                period_ms=request.data.get("period_ms"),
                duration_s=request.data.get("duration_s"),
                actor=request.user,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except DeviceCommandError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(
            {
                "status": "indicator_test command sent",
                "device": device.mac_address,
                "command_id": str(record.id),
                "payload": payload,
            }
        )

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

    @action(detail=True, methods=["get"], url_path="temperature")
    def temperature(self, request, pk=None):
        """Return recent temperature/humidity readings for charting in the UI."""
        device = self.get_object()
        since = request.query_params.get("since", "24h")
        try:
            cutoff = _parse_since_window(since)
        except ValueError:
            return Response(
                {"detail": "since must be like '24h', '7d', or an ISO timestamp."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        readings = list(
            TemperatureReading.objects.filter(
                device=device,
                recorded_at__gte=cutoff,
            ).order_by(
                "recorded_at"
            )[:1000]
        )
        latest = TemperatureReading.objects.filter(device=device).order_by("-recorded_at").first()

        return Response(
            {
                "device": device.mac_address,
                "since": cutoff.isoformat(),
                "latest_temperature_c": latest.temperature_c if latest else None,
                "latest_humidity_percent": latest.humidity_percent if latest else None,
                "readings": TemperatureReadingSerializer(readings, many=True).data,
            }
        )

    @action(
        detail=False,
        methods=["get"],
        url_path="fleet-summary",
        permission_classes=[IsAuthenticated],
    )
    def fleet_summary(self, request):
        """Aggregate fleet health for the ForgeKey dashboard.

        One round-trip: device counts (online/offline, by type, by capability,
        by firmware), an e-paper battery summary, firmware-update activity, a
        prioritised "needs attention" feed, and recent commands/updates — so the
        dashboard never has to fan out per device.
        """
        from collections import Counter

        devices = list(ESP32Device.objects.select_related("device_type").all())
        active = [d for d in devices if d.is_active]
        online = [d for d in active if d.is_online]
        offline = [d for d in active if not d.is_online]
        never_seen = [d for d in devices if d.last_seen is None]

        type_buckets: dict = {}
        for d in devices:
            code = d.device_type.code if d.device_type else "unknown"
            name = d.device_type.name if d.device_type else "Unknown"
            bucket = type_buckets.setdefault(
                code, {"code": code, "name": name, "count": 0, "online": 0}
            )
            bucket["count"] += 1
            if d.is_online:
                bucket["online"] += 1
        by_type = sorted(type_buckets.values(), key=lambda b: (-b["count"], b["name"]))

        cap_counter: Counter = Counter()
        for d in devices:
            for cap in d.capabilities or []:
                cap_counter[cap] += 1
        by_capability = [
            {"capability": cap, "count": n}
            for cap, n in sorted(cap_counter.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

        fw_counter = Counter(d.firmware_version or "unknown" for d in devices)
        by_firmware = [
            {"version": v, "count": n}
            for v, n in sorted(fw_counter.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

        low_battery_threshold = int(getattr(settings, "FORGEKEY_EPAPER_LOW_BATTERY_PERCENT", 20))
        epaper_qs = EPaperDisplay.objects.filter(is_active=True)
        epaper_total = epaper_qs.count()
        epaper_unbound = epaper_qs.filter(asset__isnull=True).count()
        low_battery_panels = list(
            epaper_qs.filter(
                battery_percent__isnull=False,
                battery_percent__lt=low_battery_threshold,
            )
            .select_related("asset")
            .order_by("battery_percent")[:20]
        )

        updates_in_flight = DeviceFirmwareUpdate.objects.filter(
            status__in=[
                DeviceFirmwareUpdate.STATUS_PENDING,
                DeviceFirmwareUpdate.STATUS_IN_PROGRESS,
            ]
        ).count()
        recent_failed = list(
            DeviceFirmwareUpdate.objects.filter(status=DeviceFirmwareUpdate.STATUS_FAILED)
            .select_related("device", "firmware_version")
            .order_by("-requested_at")[:10]
        )

        # Needs-attention feed. Offline devices are ordered never-seen first,
        # then longest-offline (oldest last_seen) first.
        offline_sorted = sorted(
            offline,
            key=lambda d: (d.last_seen is not None, d.last_seen or timezone.now()),
        )
        attention_offline = [
            {
                "kind": "offline",
                "device_id": str(d.id),
                "name": d.name or d.mac_address,
                "mac_address": d.mac_address,
                "last_seen": d.last_seen.isoformat() if d.last_seen else None,
            }
            for d in offline_sorted[:15]
        ]
        attention_low_battery = [
            {
                "kind": "low_battery",
                "display_id": str(p.id),
                "asset_name": p.asset.name if p.asset else None,
                "battery_percent": p.battery_percent,
                "last_battery_at": p.last_battery_at.isoformat() if p.last_battery_at else None,
            }
            for p in low_battery_panels
        ]
        attention_ota = [
            {
                "kind": "ota_failed",
                "device_id": str(u.device_id),
                "name": u.device.name or u.device.mac_address,
                "version": u.firmware_version.version if u.firmware_version else None,
                "error": u.error_message or "",
                "requested_at": u.requested_at.isoformat(),
            }
            for u in recent_failed
        ]

        recent_commands = [
            {
                "id": str(c.id),
                "device_id": str(c.device_id),
                "device_name": c.device.name or c.device.mac_address,
                "command": c.command,
                "ack_status": c.effective_ack_status,
                "sent_at": c.sent_at.isoformat(),
                "sent_by": c.sent_by.username if c.sent_by else None,
            }
            for c in DeviceCommand.objects.select_related("device", "sent_by").order_by("-sent_at")[
                :10
            ]
        ]
        recent_updates = [
            {
                "id": str(u.id),
                "device_id": str(u.device_id),
                "device_name": u.device.name or u.device.mac_address,
                "version": u.firmware_version.version if u.firmware_version else None,
                "status": u.status,
                "requested_at": u.requested_at.isoformat(),
                "requested_by": u.requested_by.username if u.requested_by else None,
            }
            for u in DeviceFirmwareUpdate.objects.select_related(
                "device", "firmware_version", "requested_by"
            ).order_by("-requested_at")[:10]
        ]

        return Response(
            {
                "generated_at": timezone.now().isoformat(),
                "devices": {
                    "total": len(devices),
                    "active": len(active),
                    "online": len(online),
                    "offline": len(offline),
                    "never_seen": len(never_seen),
                    "by_type": by_type,
                    "by_capability": by_capability,
                    "by_firmware": by_firmware,
                },
                "epaper": {
                    "total": epaper_total,
                    "bound": epaper_total - epaper_unbound,
                    "unbound": epaper_unbound,
                    "low_battery": len(attention_low_battery),
                },
                "firmware": {
                    "updates_in_flight": updates_in_flight,
                    "recent_failures": len(attention_ota),
                },
                "attention": {
                    "offline": attention_offline,
                    "low_battery": attention_low_battery,
                    "ota_failed": attention_ota,
                },
                "recent_commands": recent_commands,
                "recent_updates": recent_updates,
            }
        )

    def _dispatch_command(self, device, actor, payload, audit_action):
        # Persist an audit row first so the firmware can echo its UUID back
        # on the status topic and the UI can render live ack feedback. The
        # row is dropped on broker failure to avoid orphan history entries.
        actor_user = (
            actor if (actor is not None and getattr(actor, "is_authenticated", False)) else None
        )
        record = DeviceCommand.objects.create(
            device=device,
            command=audit_action or payload.get("cmd") or "unknown",
            payload={},
            sent_by=actor_user,
        )
        full_payload = dict(payload)
        full_payload["command_id"] = str(record.id)

        try:
            topic = publish_command(device, full_payload, actor=actor, audit_action=audit_action)
        except DeviceCommandError as exc:
            record.delete()
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        record.payload = full_payload
        record.save(update_fields=["payload"])
        return Response(
            {
                "status": f"{audit_action} command sent",
                "device": device.mac_address,
                "topic": topic,
                "command_id": str(record.id),
                "dispatched_at": full_payload.get("timestamp", timezone.now().isoformat()),
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

    def get_queryset(self):
        """Allow ``?asset=<id>`` so the asset detail page can fetch just the
        single OperationalMode (if any) for the asset it's showing."""
        qs = super().get_queryset()
        asset_id = self.request.query_params.get("asset")
        if asset_id:
            qs = qs.filter(asset_id=asset_id)
        return qs

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


class RoomOperationalModeViewSet(viewsets.ModelViewSet):
    """Get/set a room's admin-set operational mode (drives bound indicators)."""

    queryset = RoomOperationalMode.objects.select_related("location", "updated_by").all()
    serializer_class = RoomOperationalModeSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        """Allow ``?location=<id>`` so a room panel can fetch just its mode."""
        qs = super().get_queryset()
        location_id = self.request.query_params.get("location")
        if location_id:
            qs = qs.filter(location_id=location_id)
        return qs

    def _actor(self):
        user = self.request.user
        return user if user.is_authenticated else None

    def perform_create(self, serializer):
        serializer.save(updated_by=self._actor())

    def perform_update(self, serializer):
        serializer.save(updated_by=self._actor())


class IndicatorBindingViewSet(viewsets.ModelViewSet):
    """CRUD for indicator device ↔ asset|room bindings, plus a sync action.

    Creating a binding pushes the target's current status to the device so the
    light is correct immediately; ``RoomOperationalMode`` / status-source
    changes keep it current via signals.
    """

    queryset = IndicatorBinding.objects.select_related(
        "device", "device__device_type", "asset", "location"
    ).all()
    serializer_class = IndicatorBindingSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        """Filter by ``?device=`` / ``?asset=`` / ``?location=`` for detail pages."""
        qs = super().get_queryset()
        params = self.request.query_params
        device_id = params.get("device")
        asset_id = params.get("asset")
        location_id = params.get("location")
        if device_id:
            qs = qs.filter(device_id=device_id)
        if asset_id:
            qs = qs.filter(asset_id=asset_id)
        if location_id:
            qs = qs.filter(location_id=location_id)
        return qs

    def _actor(self):
        user = self.request.user
        return user if user.is_authenticated else None

    def perform_create(self, serializer):
        binding = serializer.save()
        record_audit_event(
            action=ForgeKeyAuditEvent.ACTION_INDICATOR_BIND,
            actor=self._actor(),
            device=binding.device,
            notes=f"bound to {'asset' if binding.asset_id else 'room'}",
            metadata={
                "asset_id": str(binding.asset_id) if binding.asset_id else None,
                "location_id": binding.location_id,
            },
        )
        # Best-effort initial push; a broker outage must not fail the bind.
        try:
            sync_indicator(binding, actor=self._actor(), force=True)
        except DeviceCommandError:
            logger.warning("Initial indicator sync failed for binding %s", binding.pk)

    def perform_destroy(self, instance):
        record_audit_event(
            action=ForgeKeyAuditEvent.ACTION_INDICATOR_UNBIND,
            actor=self._actor(),
            device=instance.device,
            notes="indicator unbound",
        )
        instance.delete()

    @action(detail=True, methods=["post"])
    def sync(self, request, pk=None):
        """Recompute the bound target's status and push it to the device."""
        binding = self.get_object()
        try:
            record = sync_indicator(binding, actor=self._actor(), force=True)
        except DeviceCommandError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(
            {
                "status": binding.last_status,
                "presentation": binding.last_presentation,
                "command_id": str(record.id) if record is not None else None,
            }
        )


class AssetAuthorizationViewSet(viewsets.ModelViewSet):
    """API endpoint for asset authorizations."""

    queryset = AssetAuthorization.objects.select_related("asset", "user", "authorized_by").all()
    serializer_class = AssetAuthorizationSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        """Support ``?asset=<id>``, ``?user=<id>`` and ``?is_active=<bool>``.

        ``?asset=`` backs the asset detail page's authorized-users list;
        ``?user=`` backs the per-member "assets I'm authorized for" view (op-tup);
        ``?is_active=`` narrows either to the currently-active grants."""
        qs = super().get_queryset()
        asset_id = self.request.query_params.get("asset")
        if asset_id:
            qs = qs.filter(asset_id=asset_id)
        user_id = self.request.query_params.get("user")
        if user_id:
            qs = qs.filter(user_id=user_id)
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() in ("1", "true", "yes"))
        return qs

    def perform_create(self, serializer):
        """Set authorized_by to current user when creating authorization."""
        actor = self.request.user
        authorization = serializer.save(authorized_by=actor)
        record_audit_event(
            action="authorization_grant",
            actor=actor,
            authorization=authorization,
            notes=authorization.notes or "",
        )

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        """Mark an authorization inactive and record the revocation in the audit log.

        Use this in preference to a DELETE: DELETE removes the row entirely so
        the audit trail loses both the original grant context and the revocation
        actor. Revoking flips ``is_active=False`` and emits an
        ``authorization_revoke`` row that points back at the (preserved)
        authorization. (gh #352)
        """
        authorization = self.get_object()
        actor = request.user

        if not actor.is_authenticated:
            return Response(
                {"error": "Authentication required"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not authorization.is_active:
            return Response(
                {"detail": "Authorization is already inactive."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        notes = (request.data.get("notes") or "").strip()
        authorization.is_active = False
        authorization.save(update_fields=["is_active"])
        record_audit_event(
            action="authorization_revoke",
            actor=actor,
            authorization=authorization,
            notes=notes,
        )

        serializer = self.get_serializer(authorization)
        return Response(serializer.data)

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

    def get_queryset(self):
        """Support ``?asset=<id>`` and ``?is_active=<bool>`` so the asset
        detail page can list just that asset's lockouts (typically the active
        ones)."""
        qs = super().get_queryset()
        asset_id = self.request.query_params.get("asset")
        if asset_id:
            qs = qs.filter(asset_id=asset_id)
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() in ("1", "true", "yes"))
        return qs

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

        lockout = serializer.save(locked_by=user, lockout_level=lockout_level)
        record_audit_event(
            action="lockout_create",
            actor=user,
            lockout=lockout,
            notes=lockout.reason or "",
            metadata={"lockout_level": lockout_level},
        )

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

        record_audit_event(
            action="lockout_unlock",
            actor=user,
            lockout=lockout,
            metadata={"lockout_level": lockout.lockout_level},
        )

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


class CertificateAuthorityViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only view of the internal CA(s) + a staff CA-rotate action.

    Device-cert issuance / revocation stays in the enrollment flow / admin;
    this surface is for visibility and rotating the root CA. Rotation mints a
    fresh self-signed root and retires the prior one — devices must be
    re-flashed (or rebuilt via the firmware pipeline) to trust the new root.
    """

    queryset = CertificateAuthority.objects.all()
    serializer_class = CertificateAuthoritySerializer
    permission_classes = [IsAdminUser]

    @action(detail=False, methods=["post"])
    def rotate(self, request):
        from .services.ca_lifecycle import mint_ca

        name = (request.data.get("name") or "forgekey-root").strip() or "forgekey-root"
        cn = (request.data.get("common_name") or "").strip() or "ForgeKey Internal Root CA"
        raw_validity = request.data.get("validity_years")
        try:
            validity_years = int(raw_validity) if raw_validity is not None else 10
        except (TypeError, ValueError):
            return Response(
                {"detail": "validity_years must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if validity_years <= 0:
            return Response(
                {"detail": "validity_years must be positive."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            ca = mint_ca(name=name, cn=cn, validity_years=validity_years, replace_active=True)
        except Exception as exc:  # noqa: BLE001 — surface any CA-gen failure
            logger.exception("CA rotation failed")
            return Response(
                {"detail": f"Failed to rotate the CA: {exc}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(CertificateAuthoritySerializer(ca).data, status=status.HTTP_201_CREATED)


class DeviceCertificateViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only list of issued device (mTLS) certificates + their status."""

    queryset = DeviceCertificate.objects.select_related("device").all()
    serializer_class = DeviceCertificateSerializer
    permission_classes = [IsAdminUser]


class FirmwareBuildViewSet(viewsets.ModelViewSet):
    """Queue + track self-hosted firmware builds (staff only).

    Creating a build enqueues ``build_firmware`` on the ``builds`` queue, which
    the dedicated firmware-builder worker consumes (clone → inject CA + pubkey
    → ``pio run`` → upload a signed FirmwareVersion). The result is then rolled
    out via the firmware-rollouts API.
    """

    queryset = FirmwareBuild.objects.select_related(
        "device_type", "firmware_version", "requested_by"
    ).all()
    serializer_class = FirmwareBuildSerializer
    permission_classes = [IsAdminUser]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def perform_create(self, serializer):
        build = serializer.save(requested_by=self.request.user)
        from .tasks import build_firmware

        build_firmware.delay(str(build.pk))

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Mark a queued / in-flight build cancelled.

        A build already executing on the worker runs to completion, but the
        worker re-checks this flag before starting and a not-yet-started build
        is skipped.
        """
        build = self.get_object()
        if build.status not in (
            FirmwareBuild.STATUS_QUEUED,
            FirmwareBuild.STATUS_BUILDING,
        ):
            return Response(
                {"detail": f"Build is {build.status}; nothing to cancel."},
                status=status.HTTP_409_CONFLICT,
            )
        build.status = FirmwareBuild.STATUS_CANCELLED
        build.completed_at = timezone.now()
        build.save(update_fields=["status", "completed_at"])
        return Response(FirmwareBuildSerializer(build).data)

    @action(detail=True, methods=["post"])
    def redispatch(self, request, pk=None):
        """Re-publish the build task to the worker queue.

        Use case: the firmware_builder worker was down (or Redis was
        restarted, dropping the in-flight message) and a row is stuck
        in ``queued`` even though the worker is now consuming. Re-
        dispatch republishes the Celery message; the same DB row is
        the target, so the audit chain stays intact.

        Allowed for ``queued`` AND ``failed`` (a failed build is often
        a transient infra issue — same row, same parameters, try again).
        Forbidden for ``building`` (the worker is mid-execution; a
        second message would race), ``succeeded`` (use ``cancel`` +
        create a new build to re-roll), and ``cancelled``.
        """
        build = self.get_object()
        if build.status not in (
            FirmwareBuild.STATUS_QUEUED,
            FirmwareBuild.STATUS_FAILED,
        ):
            return Response(
                {
                    "detail": (
                        f"Build is {build.status}; only queued / failed "
                        "builds can be re-dispatched. Create a new build "
                        "instead."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        # Reset the build so the worker treats it as a fresh attempt.
        # Otherwise a re-dispatch of a `failed` row would race the
        # previous error_message with the new run's outcome.
        build.status = FirmwareBuild.STATUS_QUEUED
        build.started_at = None
        build.completed_at = None
        build.error_message = ""
        build.log = ""
        build.save(
            update_fields=[
                "status",
                "started_at",
                "completed_at",
                "error_message",
                "log",
            ]
        )

        from .tasks import build_firmware

        build_firmware.delay(str(build.pk))
        logger.info(
            "firmware_build re-dispatched id=%s actor=%s",
            build.pk,
            getattr(request.user, "username", "<anonymous>"),
        )
        return Response(FirmwareBuildSerializer(build).data)


class FirmwareRolloutViewSet(viewsets.ModelViewSet):
    """Manage staged firmware rollout campaigns.

    Create a draft, then start it: each wave dispatches the OTA to the next
    ``batch_size_percent`` of the target fleet, advancing automatically (Celery
    beat) every ``interval_minutes``. Operators can pause, resume, cancel, or
    advance a wave by hand.
    """

    queryset = FirmwareRollout.objects.select_related(
        "firmware_version__device_type", "created_by"
    ).all()
    serializer_class = FirmwareRolloutSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def perform_create(self, serializer):
        actor = self.request.user if self.request.user.is_authenticated else None
        serializer.save(created_by=actor)

    def _serialized(self, rollout, extra=None):
        data = self.get_serializer(rollout).data
        if extra:
            data.update(extra)
        return Response(data)

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        rollout = self.get_object()
        if rollout.status not in (
            FirmwareRollout.STATUS_DRAFT,
            FirmwareRollout.STATUS_PAUSED,
        ):
            return Response(
                {"detail": f"Cannot start a {rollout.status} rollout."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if rollout.started_at is None:
            rollout.started_at = timezone.now()
        rollout.status = FirmwareRollout.STATUS_ACTIVE
        rollout.save(update_fields=["status", "started_at", "updated_at"])

        from .services.firmware_rollout import advance_rollout

        actor = request.user if request.user.is_authenticated else None
        dispatched = advance_rollout(rollout, actor=actor)
        rollout.refresh_from_db()
        return self._serialized(rollout, {"dispatched": dispatched})

    @action(detail=True, methods=["post"])
    def pause(self, request, pk=None):
        rollout = self.get_object()
        if rollout.status != FirmwareRollout.STATUS_ACTIVE:
            return Response(
                {"detail": f"Cannot pause a {rollout.status} rollout."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        rollout.status = FirmwareRollout.STATUS_PAUSED
        rollout.save(update_fields=["status", "updated_at"])
        return self._serialized(rollout)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        rollout = self.get_object()
        if rollout.status in (
            FirmwareRollout.STATUS_COMPLETED,
            FirmwareRollout.STATUS_CANCELLED,
        ):
            return Response(
                {"detail": f"Rollout already {rollout.status}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        rollout.status = FirmwareRollout.STATUS_CANCELLED
        rollout.save(update_fields=["status", "updated_at"])
        return self._serialized(rollout)

    @action(detail=True, methods=["post"])
    def advance(self, request, pk=None):
        rollout = self.get_object()
        if rollout.status != FirmwareRollout.STATUS_ACTIVE:
            return Response(
                {"detail": "Only active rollouts can be advanced."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        from .services.firmware_rollout import advance_rollout

        actor = request.user if request.user.is_authenticated else None
        dispatched = advance_rollout(rollout, actor=actor)
        rollout.refresh_from_db()
        return self._serialized(rollout, {"dispatched": dispatched})


class EpaperFirmwareRolloutViewSet(viewsets.ModelViewSet):
    """Manage ePaper firmware rollout campaigns.

    Same operator UX as ``FirmwareRolloutViewSet``: create draft, start,
    pause / cancel / advance. The dispatch model is HTTPS-pull (the panel
    checks ``/firmware-check/`` on each wake) rather than MQTT-push, so
    ``advance`` here promotes the next batch's ``target_firmware_version``
    instead of publishing OTA triggers.
    """

    queryset = EpaperFirmwareRollout.objects.select_related(
        "firmware_version__device_type", "created_by"
    ).all()
    serializer_class = EpaperFirmwareRolloutSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def perform_create(self, serializer):
        actor = self.request.user if self.request.user.is_authenticated else None
        serializer.save(created_by=actor)

    def _serialized(self, rollout, extra=None):
        data = self.get_serializer(rollout).data
        if extra:
            data.update(extra)
        return Response(data)

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        rollout = self.get_object()
        if rollout.status not in (
            EpaperFirmwareRollout.STATUS_DRAFT,
            EpaperFirmwareRollout.STATUS_PAUSED,
        ):
            return Response(
                {"detail": f"Cannot start a {rollout.status} rollout."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if rollout.started_at is None:
            rollout.started_at = timezone.now()
        rollout.status = EpaperFirmwareRollout.STATUS_ACTIVE
        rollout.save(update_fields=["status", "started_at", "updated_at"])
        return self._serialized(rollout)

    @action(detail=True, methods=["post"])
    def pause(self, request, pk=None):
        rollout = self.get_object()
        if rollout.status != EpaperFirmwareRollout.STATUS_ACTIVE:
            return Response(
                {"detail": f"Cannot pause a {rollout.status} rollout."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        rollout.status = EpaperFirmwareRollout.STATUS_PAUSED
        rollout.save(update_fields=["status", "updated_at"])
        return self._serialized(rollout)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        rollout = self.get_object()
        if rollout.status in (
            EpaperFirmwareRollout.STATUS_COMPLETED,
            EpaperFirmwareRollout.STATUS_CANCELLED,
        ):
            return Response(
                {"detail": f"Cannot cancel a {rollout.status} rollout."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        rollout.status = EpaperFirmwareRollout.STATUS_CANCELLED
        rollout.completed_at = timezone.now()
        rollout.save(update_fields=["status", "completed_at", "updated_at"])
        return self._serialized(rollout)

    @action(detail=True, methods=["post"])
    def advance(self, request, pk=None):
        rollout = self.get_object()
        if rollout.status != EpaperFirmwareRollout.STATUS_ACTIVE:
            return Response(
                {"detail": "Only active rollouts can be advanced."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        from .services.epaper_firmware_rollout import advance_rollout

        promoted = advance_rollout(rollout)
        rollout.refresh_from_db()
        return self._serialized(rollout, {"promoted": promoted})


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

        range_header = request.headers.get("range", "")
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
        # mTLS path: nginx terminates on the dedicated mTLS listener
        # (FORGEKEY_MTLS_PORT, default 8443) and forwards the verified
        # client cert as X-SSL-Client-* headers. Re-verify here so a
        # misconfigured proxy can't grant access by sending bare
        # headers, and so admin-side revoke (revoked_at) gates without
        # nginx-side CRL plumbing. This path is tried first because
        # devices on chain-aware firmware should use it exclusively;
        # token / JWT fallbacks remain for the transition window.
        mtls = verify_mtls_request(request)
        if mtls.authorized:
            return True
        if request.headers.get("x-ssl-client-verify"):
            # Headers present but rejected — log so operators can debug
            # cert issues at the proxy layer instead of silent 401s.
            logger.info("ForgeKey firmware download: mTLS rejected — %s", mtls.reason)

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


class ForgeKeyJWKSView(APIView):
    """
    GET /api/forgekey/jwks/

    Returns the active device-JWT verification public key as a JWK Set so
    EMQX can fetch + cache it and verify ES256-signed device JWTs without
    storing the key locally. Public by definition; one-hour cache lets EMQX
    pick up rotations promptly while keeping request volume negligible.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request):
        if not is_jwt_signing_configured():
            return Response(
                {"detail": "Device JWT signing is not configured."},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            jwks = get_jwt_jwks()
        except JwtSigningError as exc:
            logger.warning("Cannot serve JWKS: %s", exc)
            return Response(
                {"detail": "Device JWT signing key is misconfigured."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        # Plain HttpResponse so DRF's renderer doesn't overwrite the JWKS
        # media type with ``application/json``.
        response = HttpResponse(
            json.dumps(jwks),
            content_type="application/jwk-set+json",
        )
        response["Cache-Control"] = "public, max-age=3600"
        return response


class ForgeKeyOmsCommandPublicKeyView(APIView):
    """
    GET /api/forgekey/oms-command-public-key.pem

    Serves the OMS command-signing public key (the ES256 keypair backing
    :func:`forgekey.services.jwt_signing.make_command_jwt`). Firmware build
    scripts bake this into ``oms_command_pubkey.h`` so the device can verify
    command signatures offline. Public by definition — no auth.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request):
        if not is_jwt_signing_configured():
            return Response(
                {"detail": "OMS command-signing key is not configured."},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            pem = get_jwt_public_key_pem()
        except JwtSigningError as exc:
            logger.warning("Cannot return command public key: %s", exc)
            return Response(
                {"detail": "Command-signing key is misconfigured."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return HttpResponse(pem, content_type="application/x-pem-file")


class ForgeKeyCertificateRevocationListView(APIView):
    """
    GET /api/forgekey/ca/crl.pem

    Returns a CRL of every revoked :class:`DeviceCertificate`, signed by the
    active CA. ``thisUpdate`` is now, ``nextUpdate`` is now + 24h. Public —
    EMQX (and any other relying party) fetches this on a schedule.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request):
        active = CertificateAuthority.get_active()
        if active is None:
            return Response(
                {"detail": "No active CertificateAuthority is configured."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            private_pem = decrypt_ca_key(
                bytes(active.encrypted_private_key), active.key_kid or None
            )
            ca_private_key = serialization.load_pem_private_key(private_pem, password=None)
            ca_cert = x509.load_pem_x509_certificate(active.cert_pem.encode("utf-8"))
        except CaKeyStorageError as exc:
            logger.error("CRL: cannot unwrap active CA key: %s", exc)
            return Response(
                {"detail": "Active CA private key is not accessible."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.error("CRL: cannot parse active CA material: %s", exc)
            return Response(
                {"detail": "Active CA material is unreadable."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        if not isinstance(ca_private_key, ec.EllipticCurvePrivateKey):
            return Response(
                {"detail": "Active CA key is not an EC private key."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        now = timezone.now()
        builder = (
            x509.CertificateRevocationListBuilder()
            .issuer_name(ca_cert.subject)
            .last_update(now)
            .next_update(now + timedelta(hours=24))
        )

        revoked_qs = DeviceCertificate.objects.filter(revoked_at__isnull=False).only(
            "serial", "revoked_at"
        )
        for cert_row in revoked_qs:
            try:
                serial_int = int(cert_row.serial, 16)
            except (TypeError, ValueError):
                logger.warning("CRL: skipping revoked cert with non-hex serial %r", cert_row.serial)
                continue
            revoked = (
                x509.RevokedCertificateBuilder()
                .serial_number(serial_int)
                .revocation_date(cert_row.revoked_at)
                .build()
            )
            builder = builder.add_revoked_certificate(revoked)

        crl = builder.sign(private_key=ca_private_key, algorithm=hashes.SHA256())
        pem = crl.public_bytes(serialization.Encoding.PEM)
        response = HttpResponse(pem, content_type="application/x-pem-file")
        response["Cache-Control"] = "public, max-age=300"
        return response


def epaper_service_url(request, display_pk) -> str:
    """Front-end "log service" page a maintainer lands on from a panel QR.

    Prefer the configured ``FRONTEND_URL`` over ``request.build_absolute_uri``.
    The firmware fetches the panel image over plain HTTP from inside the
    network (the device has no kernel-level cert store), so
    ``request.scheme`` is ``"http"`` on that GET — which used to leak into
    the QR target and the operator's phone landed on http://, relying on
    the prod SECURE_SSL_REDIRECT to bounce to HTTPS. That worked but
    showed up as "no https://" in QR scanners and decoders.

    ``FRONTEND_URL`` is set per-environment in .env (e.g.
    https://dms.openmakersuite.net in prod) and is the canonical address
    the maintainer's phone would visit, so it's what the QR should point
    at. Falls back to ``request.build_absolute_uri`` when the setting is
    missing or empty so dev (no .env) still works.
    """
    from django.conf import settings

    base = (getattr(settings, "FRONTEND_URL", "") or "").strip().rstrip("/")
    path = f"/forgekey/epaper/service?did={display_pk}"
    if base:
        return f"{base}{path}"
    return request.build_absolute_uri(path)


class EPaperDisplayImageView(APIView):
    """Return the latest PM-status PNG for a XIAO 7.5" ePaper panel.

    Firmware GETs this URL on every wake-up. Responds 304 Not Modified
    when the ETag matches the device's `If-None-Match` header so a
    panel that already shows the right image can flash it back to
    deep sleep without redrawing. AllowAny because the device cannot
    carry a JWT — the ESP32 has no kernel-level certificate store
    that survives a deep-sleep cycle for this firmware class. The
    image itself contains nothing that wouldn't already be visible
    on the panel mounted on the asset.
    """

    permission_classes = [AllowAny]

    def get(self, request, display_id):
        # First contact from a panel pulled off the shelf: auto-register
        # an unbound row so the staff bind page has something to write
        # to when they scan the QR. The 409 below tells the firmware to
        # paint the bind QR. A retired (is_active=False) display does
        # not auto-resurrect — those still 404.
        try:
            display = EPaperDisplay.objects.select_related("device", "asset").get(pk=display_id)
        except EPaperDisplay.DoesNotExist:
            display = EPaperDisplay.objects.create(pk=display_id, is_active=True)
        if not display.is_active:
            return HttpResponse(status=404)
        if display.asset_id is None:
            return HttpResponse("Display unbound; no asset", status=409)

        # Lazy imports keep the boot path light — Pillow only loads
        # when a device actually hits this endpoint.
        from .services.epaper_render import compute_display_etag, render_image

        etag = compute_display_etag(display.asset, display)
        if_none_match = request.headers.get("if-none-match", "").strip().strip('"')
        if if_none_match and if_none_match == etag:
            return HttpResponse(status=304)

        # The QR encodes the front-end "log service" page for this panel
        # (mirrors the bind page's /forgekey/epaper/... route). Built from
        # the host the firmware reached us on so it stays correct across
        # dev/staging/prod without a hardcoded domain — front-end and API
        # share the origin behind nginx in prod.
        service_url = epaper_service_url(request, display.pk)
        png_bytes, _face = render_image(display.asset, display, service_url=service_url)
        # Record what version the panel just flashed; the operator
        # dashboard reads `last_image_at` to spot stale panels.
        # Always advance the rotation counter — when no rotation is in
        # play (single-face panel) the increment is harmless because
        # _pick_face only consults counter % (event+pm) and the result
        # is never read; when rotation IS active, this gives the next
        # fetch the right next face.
        EPaperDisplay.objects.filter(pk=display.pk).update(
            last_image_etag=etag,
            last_image_at=timezone.now(),
            rotation_counter=F("rotation_counter") + 1,
        )
        response = HttpResponse(png_bytes, content_type="image/png")
        response["ETag"] = f'"{etag}"'
        response["Cache-Control"] = "no-cache, must-revalidate"
        return response


def _serialize_loto(asset) -> dict:
    """Lockout/tagout payload for the work-order page: free-form
    instructions plus each energy source to isolate and the lock devices
    it needs. Stale (de-derived) sources are omitted.
    """
    sources = asset.energy_sources.filter(is_stale=False).prefetch_related("required_devices")
    return {
        "instructions": getattr(asset, "lockout_instructions", "") or "",
        "energy_sources": [
            {
                "source_type": source.source_type,
                "source_type_display": source.get_source_type_display(),
                "magnitude": source.magnitude or "",
                "isolation_point": source.isolation_point or "",
                "notes": source.notes or "",
                "devices": [
                    {"device_type": device.get_device_type_display(), "label": device.label}
                    for device in source.required_devices.all()
                ],
            }
            for source in sources
        ],
    }


def _supply_location(obj) -> dict:
    """Where a tool/consumable lives + how many are on hand.

    Prefers the linked inventory item (real storage location, shelf, and live
    stock count); falls back to the free-text ``location_hint`` when nothing
    is linked. Keeps the e-paper work order honest about where to find the
    thing without making the maintainer go hunting.
    """
    inv = getattr(obj, "inventory_item", None)
    if inv is not None:
        loc = inv.location.name if inv.location_id else ""
        shelf = inv.get_shelf_position_display() if inv.shelf_position else ""
        where = f"{loc} · {shelf}" if loc and shelf else (loc or shelf)
        return {
            "location": where or "",
            "on_hand": inv.current_stock,
            "sku": inv.sku or "",
            "inventory_item_id": str(inv.pk),
        }
    return {
        "location": getattr(obj, "location_hint", "") or "",
        "on_hand": None,
        "sku": "",
        "inventory_item_id": None,
    }


def _serialize_power(asset) -> dict:
    """Where the asset's power comes from + how to kill it.

    Resolves the structured breaker→panel→location and disconnect→location
    chains so the maintainer can walk straight to the breaker before starting,
    falling back to the legacy free-text fields (``breaker_location``,
    ``electrical_box``, ``suite``) when the structured links aren't set.
    """
    breaker = asset.breaker
    disconnect = asset.disconnect
    panel = breaker.panel if breaker else None
    breaker_block = None
    if breaker is not None:
        breaker_block = {
            "label": breaker.label or "",
            "position": breaker.position or "",
            "amperage": breaker.amperage,
            "panel": panel.name if panel else "",
            "panel_location": (panel.location.name if panel and panel.location_id else ""),
        }
    disconnect_block = None
    if disconnect is not None:
        disconnect_block = {
            "label": disconnect.label or "",
            "type": disconnect.get_disconnect_type_display(),
            "location": (disconnect.location.name if disconnect.location_id else ""),
        }
    return {
        "wiring_type": asset.get_wiring_type_display() if asset.wiring_type else "",
        "breaker": breaker_block,
        "disconnect": disconnect_block,
        "breaker_location": asset.breaker_location or "",
        "electrical_box": asset.electrical_box or "",
        "suite": asset.suite or "",
    }


class EPaperServiceInfoView(APIView):
    """What-needs-doing payload for the scan-to-log front-end page.

    AllowAny: reading the task is as public as the panel face mounted on
    the asset. Logging the work (``/complete/``) requires auth so the
    MaintenanceLog is attributable.
    """

    permission_classes = [AllowAny]

    def get(self, request, display_id):
        try:
            display = EPaperDisplay.objects.select_related(
                "asset",
                "asset__location",
                "asset__breaker__panel__location",
                "asset__disconnect__location",
            ).get(pk=display_id)
        except EPaperDisplay.DoesNotExist:
            return Response({"detail": "Unknown display."}, status=status.HTTP_404_NOT_FOUND)
        if not display.is_active:
            return Response({"detail": "Display retired."}, status=status.HTTP_404_NOT_FOUND)
        if display.asset_id is None:
            return Response(
                {"detail": "Display is not bound to an asset.", "bound": False},
                status=status.HTTP_409_CONFLICT,
            )

        from .services.epaper_render import (
            _days_until_due,
            _item_status,
            _next_due_item,
            _recurring_items,
            _status_line,
        )

        asset = display.asset
        items = _recurring_items(asset)
        primary = _next_due_item(asset)

        def serialize(item):
            return {
                "id": str(item.pk),
                "title": item.title,
                "interval_days": item.interval_days,
                "status": _item_status(item),
                "days_until_due": _days_until_due(item),
                "status_line": _status_line(item),
                "last_completed": (
                    item.last_completed_at.date().isoformat() if item.last_completed_at else None
                ),
                "instructions": item.instructions or "",
                "estimated_time_minutes": item.estimated_time_minutes,
                # Ordered checklist of what to actually do (printed-work-order parity).
                "steps": [
                    {
                        "order": task.order,
                        "title": task.title,
                        "description": task.description or "",
                        "is_required": task.is_required,
                    }
                    for task in item.tasks.order_by("order", "title")
                ],
                # Tools to gather (not consumed) + where each one lives.
                "tools": [
                    {
                        "name": tool.name,
                        "quantity": tool.quantity,
                        "is_required": tool.is_required,
                        "notes": tool.notes or "",
                        **_supply_location(tool),
                    }
                    for tool in item.tools.all()
                ],
                # Consumables/supplies used for the job, with where they're
                # stocked + how many are on hand.
                "materials": [
                    {
                        "name": material.name,
                        "quantity": (
                            str(material.quantity) if material.quantity is not None else None
                        ),
                        "unit": material.unit or "",
                        **_supply_location(material),
                    }
                    for material in item.materials.all()
                ],
            }

        return Response(
            {
                "display_id": str(display.pk),
                "bound": True,
                "asset": {
                    "id": str(asset.pk),
                    "name": asset.name,
                    "asset_tag": getattr(asset, "asset_tag", "") or "",
                    "location": asset.location.name if asset.location_id else None,
                    "location_id": str(asset.location_id) if asset.location_id else None,
                },
                # Where the asset's power is + how to kill it before servicing.
                "power": _serialize_power(asset),
                # Asset-level lockout/tagout: free-form instructions plus the
                # energy sources to isolate and the lock devices needed.
                "loto": _serialize_loto(asset),
                "items": [serialize(i) for i in items],
                "primary_item_id": str(primary.pk) if primary else None,
            }
        )


class EPaperServiceCompleteView(APIView):
    """Log a PM service from the scanned panel page.

    Login required (any member) so ``MaintenanceLog.completed_by`` records
    who did the work. Logging a service shifts the asset's ETag, so the
    panel paints the reset countdown on its next wake.
    """

    permission_classes = [IsAuthenticated]
    # Accept JSON (notes-only) and multipart (when a photo of the work is
    # attached) on the same endpoint.
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request, display_id):
        try:
            display = EPaperDisplay.objects.select_related("asset", "asset__location").get(
                pk=display_id
            )
        except EPaperDisplay.DoesNotExist:
            return Response({"detail": "Unknown display."}, status=status.HTTP_404_NOT_FOUND)
        if not display.is_active or display.asset_id is None:
            return Response(
                {"detail": "Display is not bound to an active asset."},
                status=status.HTTP_409_CONFLICT,
            )

        from inventory.models import Location, MaintenanceLog, MaintenanceLogPhoto

        from .services.epaper_render import _item_status, _next_due_item, _status_line

        active = display.asset.maintenance_items.filter(is_active=True)
        item_id = request.data.get("item_id")
        if item_id:
            item = active.filter(pk=item_id).first()
        else:
            item = _next_due_item(display.asset)
        if item is None:
            return Response(
                {"detail": "No matching active maintenance task for this asset."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Where the work happened — default to the asset's location, but let
        # the maintainer override (e.g., the asset was moved to another room).
        location = display.asset.location
        location_id = request.data.get("location_id")
        if location_id:
            location = Location.objects.filter(pk=location_id).first() or location

        # Mirror inventory's MaintenanceItem.complete action: log the
        # completion (attributed) and roll the item's due date forward.
        notes = (request.data.get("notes") or "").strip()
        log = MaintenanceLog.objects.create(
            maintenance_item=item,
            completed_by=request.user,
            location=location,
            notes=notes,
        )

        # Optional photo of the work performed (multipart ``photo`` field).
        photo_file = request.FILES.get("photo")
        photo_attached = False
        if photo_file is not None:
            MaintenanceLogPhoto.objects.create(
                maintenance_log=log,
                image=photo_file,
                uploaded_by=request.user,
            )
            photo_attached = True

        item.last_completed_at = log.completed_at
        item.save(update_fields=["last_completed_at"])
        return Response(
            {
                "ok": True,
                "item_id": str(item.pk),
                "title": item.title,
                "status": _item_status(item),
                "status_line": _status_line(item),
                "completed_at": log.completed_at.isoformat(),
                "completed_by": getattr(request.user, "username", None),
                "location": location.name if location else None,
                "photo_attached": photo_attached,
            },
            status=status.HTTP_201_CREATED,
        )


class EPaperDisplayBatteryView(APIView):
    """Receive a battery-percent telemetry report from an ePaper panel.

    Body: ``{"percent": 0..100}``. Stores the value + timestamp, and
    captures a Sentry warning when the panel drops below the
    low-battery threshold so ops can prep a charged swap before the
    panel goes dark. AllowAny for the same reasons as the image
    endpoint — the firmware has no auth credential.
    """

    permission_classes = [AllowAny]

    def post(self, request, display_id):
        try:
            display = EPaperDisplay.objects.select_related("device", "asset").get(
                pk=display_id, is_active=True
            )
        except EPaperDisplay.DoesNotExist:
            return HttpResponse(status=404)

        try:
            percent = int(request.data.get("percent"))
        except (TypeError, ValueError):
            return Response({"detail": "percent must be an integer 0..100"}, status=400)
        if percent < 0 or percent > 100:
            return Response({"detail": "percent must be 0..100"}, status=400)

        EPaperDisplay.objects.filter(pk=display.pk).update(
            battery_percent=percent,
            last_battery_at=timezone.now(),
        )
        display.refresh_from_db()

        # Sentry warning when crossing the low-battery threshold —
        # ops dashboards can subscribe to this and prep a charged
        # swap before the panel dies.
        if display.is_low_battery:
            asset_name = display.asset.name if display.asset_id else "unbound"
            with sentry_sdk.new_scope() as scope:
                scope.set_tag("forgekey.device_mac", display.device.mac_address)
                scope.set_tag("forgekey.display_id", str(display.pk))
                scope.set_tag("forgekey.asset", asset_name)
                scope.set_extra("battery_percent", percent)
                sentry_sdk.capture_message(
                    f"ePaper panel low battery: {percent}% on {asset_name}",
                    level="warning",
                )
        return Response({"battery_percent": percent}, status=200)


class EPaperDisplayHealthView(APIView):
    """Receive the full wake-cycle health payload from an ePaper panel.

    The firmware posts a wider envelope than ``/battery/`` (battery is
    one nested field inside it): firmware version, last-image etag,
    consecutive-unchanged + consecutive-failure counters, render
    status, retired flag, and the per-subsystem ``ota`` / ``board`` /
    ``power`` blocks each capability appends. We persist the fields
    the model has columns for (battery%, firmware_version,
    last_image_etag) and accept the rest so the firmware's POST
    succeeds instead of 404-ing into the serial log.

    Battery handling mirrors :class:`EPaperDisplayBatteryView` — same
    low-battery Sentry alert, same upsert semantics — so the existing
    ``/battery/`` endpoint stays a strict subset of this one.

    AllowAny for the same reason as the image / battery endpoints:
    the panel firmware has no auth credential. The display_id in the
    URL is the only handle; if it doesn't match a row we 404.
    """

    permission_classes = [AllowAny]

    def post(self, request, display_id):
        try:
            display = EPaperDisplay.objects.select_related("device", "asset").get(
                pk=display_id, is_active=True
            )
        except EPaperDisplay.DoesNotExist:
            return HttpResponse(status=404)

        body = request.data if isinstance(request.data, dict) else {}
        updates = {
            # We got a health envelope — stamp it even when no individual
            # field below changes, so the dashboard can tell "panel is
            # checking in" apart from "no contact since enroll".
            "last_health_at": timezone.now(),
        }

        # Battery — same coerce-and-validate as the /battery/ endpoint.
        # Tolerate the firmware sending power={} (battery unavailable
        # on the stock SKU-6416 panel) without flagging an error.
        battery_percent = None
        power_block = body.get("power") if isinstance(body.get("power"), dict) else {}
        battery_block = (
            power_block.get("battery") if isinstance(power_block.get("battery"), dict) else {}
        )
        raw_percent = battery_block.get("percent")
        if raw_percent is not None:
            try:
                battery_percent = int(raw_percent)
            except (TypeError, ValueError):
                return Response(
                    {"detail": "power.battery.percent must be an integer 0..100"}, status=400
                )
            if battery_percent < 0 or battery_percent > 100:
                return Response({"detail": "power.battery.percent must be 0..100"}, status=400)
            updates["battery_percent"] = battery_percent
            updates["last_battery_at"] = timezone.now()

        # Battery sensor presence — separately from the percent so the
        # dashboard can distinguish "SKU 6416 hardware has no ADC line"
        # from "panel hasn't reported yet". Trust the firmware's own
        # claim: available may be absent (older firmware) or explicit
        # true/false on current main.
        raw_available = battery_block.get("available")
        if isinstance(raw_available, bool):
            updates["battery_available"] = raw_available
            reason = battery_block.get("reason")
            updates["battery_unavailable_reason"] = (
                reason[:64] if (not raw_available and isinstance(reason, str)) else ""
            )

        firmware_version = body.get("firmware_version")
        if isinstance(firmware_version, str) and firmware_version:
            updates["firmware_version"] = firmware_version[:50]

        etag = body.get("last_image_etag")
        if isinstance(etag, str) and etag:
            updates["last_image_etag"] = etag[:64]

        EPaperDisplay.objects.filter(pk=display.pk).update(**updates)
        display.refresh_from_db()

        # Low-battery Sentry alert — only when battery was reported
        # this cycle. `device` is nullable (HTTPS-only panels skip the
        # MAC-keyed enrollment path) so guard the mac tag.
        if battery_percent is not None and display.is_low_battery:
            asset_name = display.asset.name if display.asset_id else "unbound"
            with sentry_sdk.new_scope() as scope:
                if display.device_id is not None:
                    scope.set_tag("forgekey.device_mac", display.device.mac_address)
                scope.set_tag("forgekey.display_id", str(display.pk))
                scope.set_tag("forgekey.asset", asset_name)
                scope.set_extra("battery_percent", battery_percent)
                sentry_sdk.capture_message(
                    f"ePaper panel low battery: {battery_percent}% on {asset_name}",
                    level="warning",
                )
        return Response(
            {
                "display_id": str(display.pk),
                "battery_percent": display.battery_percent,
                "firmware_version": display.firmware_version,
            },
            status=200,
        )


class EPaperDisplayDesiredView(APIView):
    """Return the panel's desired runtime state on each wake.

    Firmware GETs this right after the OTA check; it applies the
    cadence (``wake_min``) for the next deep-sleep duration and reacts
    to one-shot flags (``force_refresh``, ``retired``, ``factory_reset``).

    Today we return a single static cadence — ``wake_min`` — sourced
    from the ``FORGEKEY_EPAPER_DEFAULT_WAKE_MIN`` setting. Per-panel
    cadence override is a follow-up (model field + admin) but the
    endpoint shape is stable so the firmware never needs to relearn it.

    Returns 204 (no changes) when the display has no overrides, 200
    with the desired-state JSON when something is set. Both halves
    are valid no-op responses from the firmware's perspective —
    keeping the 204 path means the wake log says ``no desired-state
    changes`` instead of always painting it as a state push.

    AllowAny: no auth credential on the panel.
    """

    permission_classes = [AllowAny]

    def get(self, request, display_id):
        try:
            display = EPaperDisplay.objects.get(pk=display_id, is_active=True)
        except EPaperDisplay.DoesNotExist:
            return HttpResponse(status=404)

        wake_min = getattr(settings, "FORGEKEY_EPAPER_DEFAULT_WAKE_MIN", 60)
        return Response(
            {
                "display_id": str(display.pk),
                "desired": {"wake_min": int(wake_min)},
            },
            status=200,
        )


class EPaperDisplayCommandAckView(APIView):
    """Accept a command-ack POST from the panel.

    The firmware POSTs to ``/command/status/`` after executing a
    command pulled from the desired-state payload (force_refresh,
    retire, etc.). We don't yet issue commands so this is a no-op
    accept — exists only to stop the 404 noise in the panel's serial
    log and clear the way to surface ack timing later.
    """

    permission_classes = [AllowAny]

    def post(self, request, display_id):
        if not EPaperDisplay.objects.filter(pk=display_id, is_active=True).exists():
            return HttpResponse(status=404)
        return Response(status=204)


class EPaperDisplayFirmwareStatusView(APIView):
    """Accept a firmware-progress POST from the panel.

    The firmware POSTs to ``/firmware/status/`` during an OTA download
    (started / progress / completed / failed). The panel-side OTA
    progress isn't visualised in the admin yet, but ``firmware-check``
    already stamps the running version on success, so this endpoint
    is a no-op accept that exists to stop the 404 in the wake log.
    """

    permission_classes = [AllowAny]

    def post(self, request, display_id):
        if not EPaperDisplay.objects.filter(pk=display_id, is_active=True).exists():
            return HttpResponse(status=404)
        return Response(status=204)


class EPaperFirmwareCheckView(APIView):
    """Check whether the ePaper panel has firmware to install on this wake.

    Called by the panel firmware right after wake, before the image fetch.
    The panel passes its currently-running version as ``?current=<v>``;
    we look at ``EPaperDisplay.target_firmware_version`` (populated in
    waves by ``EpaperFirmwareRollout``):

    * No target set, OR target's version matches ``current`` → 204
      (panel is on the right firmware, proceed to image fetch).
    * Target differs → 200 with the metadata the panel needs to install:
      ``{version, url, sha256, signature, signing_cert, mandatory}``.
      ``url`` carries a short-lived HMAC token so the download endpoint
      accepts the (anonymous) panel without a device-JWT or mTLS cert.

    Side effect on every call: stamp the reported ``current`` onto
    ``EPaperDisplay.firmware_version`` so the admin can see what's
    actually installed in the field.

    AllowAny matches the other ePaper endpoints — these panels skip
    MQTT enrollment + mTLS by design (display_id is the identity).
    """

    permission_classes = [AllowAny]

    def get(self, request, display_id):
        try:
            display = EPaperDisplay.objects.select_related("target_firmware_version").get(
                pk=display_id, is_active=True
            )
        except EPaperDisplay.DoesNotExist:
            return HttpResponse(status=404)

        current = (request.query_params.get("current") or "").strip()
        if current and current != display.firmware_version:
            EPaperDisplay.objects.filter(pk=display.pk).update(firmware_version=current)

        target = display.target_firmware_version
        if target is None or target.version == current:
            return HttpResponse(status=204)

        from .models import FirmwareSigningKey
        from .services.firmware_download_token import make_download_token

        token, expiry = make_download_token(str(target.pk))
        base_url = target.effective_binary_url
        if not base_url:
            # Active row but no file — shouldn't happen, but don't crash.
            logger.warning("EPaperFirmwareCheckView: target %s has no binary URL", target.pk)
            return HttpResponse(status=503)
        # Append our token query so the download endpoint accepts the call.
        sep = "&" if "?" in base_url else "?"
        url = f"{base_url}{sep}token={token}&exp={expiry}"

        payload = {
            "version": target.version,
            "url": url,
            "sha256": target.sha256,
            "signature": target.signature or "",
            "mandatory": target.mandatory,
        }
        active_key = FirmwareSigningKey.get_active()
        if active_key is not None and active_key.cert_pem:
            payload["signing_cert"] = active_key.cert_pem
        return Response(payload, status=200)


class EPaperDisplayBindView(APIView):
    """Bind (or re-bind) an ePaper display to an asset.

    Called from the mobile bind page after staff scans the QR on an
    unbound panel and picks an asset from the searchable list. The
    display_id is the UUID the firmware generated at first boot and
    rendered into the QR. Auto-creates the display row on first call
    so the picker page doesn't need to coordinate with the firmware's
    initial image.png fetch. Re-bind is a plain PATCH of the asset FK
    — useful when a panel moves from one machine to another.

    Requires staff JWT. Anyone with the QR + a logged-in OMS session
    can bind, which matches our floor-trust model: physical access to
    the panel is the gate, and a rebind is reversible.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, display_id):
        from inventory.models import Asset

        asset_id = request.data.get("asset_id")
        if not asset_id:
            return Response({"detail": "asset_id required"}, status=400)
        try:
            asset = Asset.objects.get(pk=asset_id)
        except (Asset.DoesNotExist, ValueError):
            return Response({"detail": "asset not found"}, status=404)

        display, _ = EPaperDisplay.objects.get_or_create(
            pk=display_id,
            defaults={"is_active": True},
        )
        if not display.is_active:
            return Response({"detail": "display is retired"}, status=410)

        display.asset = asset
        display.save(update_fields=["asset", "updated_at"])

        return Response(
            {
                "display_id": str(display.pk),
                "asset_id": str(asset.pk),
                "asset_name": asset.name,
            },
            status=200,
        )


class EPaperDisplayListView(APIView):
    """List every ePaper panel for the management screen.

    Staff use this to see each panel's asset binding, battery level, last
    image fetch, and active/retired state at a glance. Rebinding reuses the
    existing bind endpoint; retiring uses EPaperDisplaySetActiveView below.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        displays = EPaperDisplay.objects.select_related("asset", "device").all()
        return Response(EPaperDisplaySerializer(displays, many=True).data)


class EPaperDisplaySetActiveView(APIView):
    """Retire or reactivate an ePaper panel.

    ``POST {"is_active": false}`` retires the panel — the firmware's image.png
    fetch then paints the "retired" card and the panel stops refreshing.
    ``{"is_active": true}`` brings it back into service.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, display_id):
        try:
            display = EPaperDisplay.objects.get(pk=display_id)
        except EPaperDisplay.DoesNotExist:
            return Response({"detail": "Display not found."}, status=status.HTTP_404_NOT_FOUND)

        raw = request.data.get("is_active", False)
        if isinstance(raw, str):
            is_active = raw.strip().lower() in {"true", "1", "yes"}
        else:
            is_active = bool(raw)

        display.is_active = is_active
        display.save(update_fields=["is_active", "updated_at"])
        return Response(EPaperDisplaySerializer(display).data)


class EPaperDisplaySetRotationView(APIView):
    """Set the per-panel rotation weights for the OOS/reservation/PM picker.

    ``POST {"event_face_weight": 2, "pm_face_weight": 1}``. Both fields
    are optional; the omitted one stays put. Values are coerced to
    non-negative ints and clipped at 100 (anything beyond that produces
    a useless cycle length). Both at 0 falls back to PM in
    ``_pick_face``, so the operator can use the same surface to switch
    rotation off entirely.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, display_id):
        try:
            display = EPaperDisplay.objects.get(pk=display_id)
        except EPaperDisplay.DoesNotExist:
            return Response({"detail": "Display not found."}, status=status.HTTP_404_NOT_FOUND)

        update_fields: list[str] = ["updated_at"]
        for field in ("event_face_weight", "pm_face_weight"):
            if field not in request.data:
                continue
            try:
                raw = int(request.data[field])
            except (TypeError, ValueError):
                return Response(
                    {"detail": f"{field} must be an integer."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if raw < 0:
                return Response(
                    {"detail": f"{field} must be non-negative."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            setattr(display, field, min(raw, 100))
            update_fields.append(field)

        if len(update_fields) == 1:
            # Nothing was sent — surface the current row anyway so the
            # caller doesn't need to round-trip a separate GET.
            return Response(EPaperDisplaySerializer(display).data)

        display.save(update_fields=update_fields)
        return Response(EPaperDisplaySerializer(display).data)
