"""DRF views for the Lockers app (Phase 3).

Only the webhook receivers are exposed here — admin / staff CRUD on
Locker / LockerDevice / LockerOtp goes through Django admin in
Phase 1+2 and will get a richer DRF API in Phase 4.

The webhook endpoints are intentionally thin: they validate the
payload, persist enough state to drive the dashboards, and emit a
ForgeKey audit event. Phase 4 wraps them with `LockerAccessEvent`
state-machine transitions.
"""

from __future__ import annotations

import logging

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from forgekey.models import ESP32Device
from forgekey.services.device_commands import DeviceCommandError

from .models import Locker, LockerOtp, LockerStatus
from .serializers import (
    IrBreakEventSerializer,
    LockerDetailSerializer,
    LockerOtpSerializer,
    LockoutEventSerializer,
    LockStatusEventSerializer,
    ReedStatusEventSerializer,
)
from .services.access import (
    OtpDenied,
    can_user_manage_locker,
    decide_locker_access,
    generate_otp,
)
from .services.commands import NoSuchLockerDevice, publish_unlock

logger = logging.getLogger(__name__)


def _locker_for_mac(mac: str):
    """Resolve `(mac) -> Locker` via the LockerDevice link table.

    Returns None when the MAC is unknown or not yet bound to a locker
    so views can return 404 without leaking enumeration.
    """
    return (
        Locker.objects.filter(
            device_assignments__device__mac_address__iexact=mac,
            is_active=True,
        )
        .distinct()
        .first()
    )


class _WebhookBase(APIView):
    """Base class for EMQX rule-engine webhook receivers.

    EMQX forwards the rule-engine output as an authenticated HTTP POST
    using a Django session / API token; the `IsAuthenticated`
    permission keeps anonymous traffic out. Per-route signature
    verification (the EMQX bridge token) is layered on in Phase 3 if
    operators run EMQX outside the cluster trust boundary.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = None  # overridden by subclasses
    event_kind: str = ""  # logged + future LockerAccessEvent.kind

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        locker = _locker_for_mac(data["mac"])
        if locker is None:
            logger.info("Locker webhook %s: unknown MAC %s", self.event_kind, data["mac"])
            return Response(
                {"detail": "Unknown MAC for any active locker."},
                status=status.HTTP_404_NOT_FOUND,
            )

        self.handle(locker=locker, payload=data)
        return Response({"status": "accepted"}, status=status.HTTP_202_ACCEPTED)

    def handle(self, *, locker: Locker, payload: dict) -> None:
        """Subclasses log the event and (later) persist a
        LockerAccessEvent row. Phase 3 just logs; Phase 4 fills it in.
        """
        logger.info(
            "Locker webhook %s for %s: %s",
            self.event_kind,
            locker.slug,
            {k: v for k, v in payload.items() if k != "mac"},
        )


class LockoutEventView(_WebhookBase):
    serializer_class = LockoutEventSerializer
    event_kind = "lockout"


class IrBreakEventView(_WebhookBase):
    serializer_class = IrBreakEventSerializer
    event_kind = "ir_break"


class ReedStatusEventView(_WebhookBase):
    serializer_class = ReedStatusEventSerializer
    event_kind = "reed_status"


class LockStatusEventView(_WebhookBase):
    """Ingest the firmware's comprehensive ``cabinet_lock/status`` heartbeat
    and upsert the locker's latest :class:`LockerStatus`."""

    serializer_class = LockStatusEventSerializer
    event_kind = "lock_status"

    def handle(self, *, locker: Locker, payload: dict) -> None:
        device = ESP32Device.objects.filter(mac_address__iexact=payload["mac"]).first()
        raw = {k: v for k, v in payload.items() if k not in ("mac", "timestamp")}
        LockerStatus.objects.update_or_create(
            locker=locker,
            defaults={
                "device": device,
                "secure": payload.get("secure"),
                "state": payload.get("state") or "",
                "reed_closed": payload.get("reed_closed"),
                "latch_locked": payload.get("latch_locked"),
                "ir_broken": payload.get("ir_broken"),
                "mortise_active": payload.get("mortise_active"),
                "item_present": payload.get("item_present"),
                "last_trigger": payload.get("last_trigger") or "",
                "firmware_version": payload.get("firmware_version") or "",
                "raw_payload": raw,
            },
        )
        super().handle(locker=locker, payload=payload)


# ---------------------------------------------------------------------------
# Init-handshake ack
# ---------------------------------------------------------------------------


class RegistrationAckView(APIView):
    """Receive an `init_ack` request from the firmware bring-up path.

    When a freshly-flashed locker boots and registers, EMQX or the
    `mqtt_consumer` forwards a "registered" notification here. This
    endpoint replies with an ack that tells the firmware to stop its
    initialization LED pattern and enter normal-operation mode.

    The actual MQTT publish is delegated to
    `lockers.services.commands.publish_init_ack`. The DRF view just
    handles the HTTP-side handshake from the bridge.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        mac = request.data.get("mac")
        if not mac:
            return Response(
                {"detail": "mac is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        locker = _locker_for_mac(mac)
        if locker is None:
            return Response(
                {"detail": "Unknown MAC for any active locker."},
                status=status.HTTP_404_NOT_FOUND,
            )

        from .services.commands import NoSuchLockerDevice, publish_init_ack

        try:
            topic = publish_init_ack(locker=locker)
        except NoSuchLockerDevice as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )

        # Touch the device row so the dashboard shows a fresh last_seen
        # without waiting for the next heartbeat.
        ESP32Device.objects.filter(mac_address__iexact=mac).update(is_online=True)

        return Response(
            {"status": "ack_published", "topic": topic},
            status=status.HTTP_202_ACCEPTED,
        )


class LockerViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only locker list + detail with bound devices and live lock status.

    The monitoring surface for the locker fleet — each locker carries its
    device bindings and its latest ``cabinet_lock/status`` so the UI can show
    secure / online state and flag possible intrusions.
    """

    queryset = (
        Locker.objects.select_related("location", "owning_sig", "current_asset", "status")
        .prefetch_related("device_assignments__device")
        .all()
    )
    serializer_class = LockerDetailSerializer
    permission_classes = [IsAuthenticated]

    @action(detail=True, methods=["post"])
    def unlock(self, request, pk=None):
        """Sign + publish the ES256 unlock command to the latch device, gated
        by the locker access decision and audited via ``publish_unlock``."""
        locker = self.get_object()
        decision = decide_locker_access(request.user, locker)
        if not decision.allowed:
            return Response(
                {
                    "detail": f"Access denied ({decision.reason}).",
                    "reason": decision.reason,
                    "missing_certifications": list(decision.missing_certifications),
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            topic = publish_unlock(locker=locker, actor=request.user)
        except NoSuchLockerDevice as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except DeviceCommandError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({"status": "unlock_sent", "topic": topic, "reason": decision.reason})

    @action(detail=True, methods=["post"], url_path="issue-otp")
    def issue_otp(self, request, pk=None):
        """Mint a one-time access code, gated by the same access decision as a
        direct unlock. Returns the code (the bearer credential)."""
        locker = self.get_object()
        try:
            otp = generate_otp(user=request.user, locker=locker)
        except OtpDenied as exc:
            return Response(
                {
                    "detail": f"Access denied ({exc.decision.reason}).",
                    "reason": exc.decision.reason,
                    "missing_certifications": list(exc.decision.missing_certifications),
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(LockerOtpSerializer(otp).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def otps(self, request, pk=None):
        """List recent OTPs for the locker (managers only)."""
        locker = self.get_object()
        if not can_user_manage_locker(request.user, locker):
            return Response(
                {"detail": "Only locker managers may view OTPs."},
                status=status.HTTP_403_FORBIDDEN,
            )
        rows = locker.otps.select_related("requesting_user", "revoked_by").order_by("-created_at")[
            :50
        ]
        return Response(LockerOtpSerializer(rows, many=True).data)

    @action(detail=True, methods=["post"], url_path="revoke-otp")
    def revoke_otp(self, request, pk=None):
        """Revoke an outstanding OTP (managers only)."""
        locker = self.get_object()
        if not can_user_manage_locker(request.user, locker):
            return Response(
                {"detail": "Only locker managers may revoke OTPs."},
                status=status.HTTP_403_FORBIDDEN,
            )
        otp_id = request.data.get("otp_id")
        if not otp_id:
            return Response({"detail": "otp_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            otp = locker.otps.get(id=otp_id)
        except LockerOtp.DoesNotExist:
            return Response(
                {"detail": "OTP not found for this locker."},
                status=status.HTTP_404_NOT_FOUND,
            )
        otp.revoke(by=request.user)
        return Response(LockerOtpSerializer(otp).data)
