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
    DeviceCommandSerializer,
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
from .services.ca_key_storage import CaKeyStorageError, decrypt_ca_key
from .services.csr_signing import CsrSigningError, CsrValidationError, sign_csr
from .services.device_commands import DeviceCommandError, publish_command
from .services.firmware_download_token import verify_download_token
from .services.firmware_signing import (
    FirmwareSigningError,
    get_public_key_pem,
    is_signing_configured,
)
from .services.jwt_signing import (
    JwtSigningError,
    get_jwt_jwks,
    get_jwt_public_key_pem,
    is_jwt_signing_configured,
)
from .tasks import (
    disable_device,
    enable_device,
    process_mqtt_device_capabilities,
    process_mqtt_firmware_update_response,
    process_mqtt_occupancy,
    process_mqtt_power_reading,
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
        try:
            display = EPaperDisplay.objects.select_related("device", "asset").get(
                pk=display_id, is_active=True
            )
        except EPaperDisplay.DoesNotExist:
            return HttpResponse(status=404)
        if display.asset_id is None:
            return HttpResponse("Display unbound; no asset", status=409)

        # Lazy imports keep the boot path light — Pillow only loads
        # when a device actually hits this endpoint.
        from .services.epaper_render import compute_snapshot_etag, render_pm_image

        etag = compute_snapshot_etag(display.asset)
        if_none_match = request.headers.get("if-none-match", "").strip().strip('"')
        if if_none_match and if_none_match == etag:
            return HttpResponse(status=304)

        png_bytes = render_pm_image(display.asset)
        # Record what version the panel just flashed; the operator
        # dashboard reads `last_image_at` to spot stale panels.
        EPaperDisplay.objects.filter(pk=display.pk).update(
            last_image_etag=etag,
            last_image_at=timezone.now(),
        )
        response = HttpResponse(png_bytes, content_type="image/png")
        response["ETag"] = f'"{etag}"'
        response["Cache-Control"] = "no-cache, must-revalidate"
        return response


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
