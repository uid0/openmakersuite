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

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.observability_redaction import redact
from forgekey.models import ESP32Device

from .models import (
    Locker,
    LockerAccessEvent,
    LockerAccessEventKind,
    LockerHighTrustReturn,
)
from .serializers import (
    IrBreakEventSerializer,
    LockoutEventSerializer,
    ReedStatusEventSerializer,
)

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
    """Persist a TAMPER row when entering lockout, LOCKOUT_CLEARED on exit."""

    serializer_class = LockoutEventSerializer
    event_kind = "lockout"

    def handle(self, *, locker: Locker, payload: dict) -> None:
        super().handle(locker=locker, payload=payload)
        kind = (
            LockerAccessEventKind.TAMPER
            if payload["state"] == "entered"
            else LockerAccessEventKind.LOCKOUT_CLEARED
        )
        LockerAccessEvent.objects.create(
            locker=locker,
            kind=kind,
            asset=locker.current_asset,
            metadata=redact(payload),
        )


class IrBreakEventView(_WebhookBase):
    """An IR-break event in isolation is just telemetry. It only becomes an
    ITEM_REMOVED row when the door is *open* at the same time — see Phase 4
    correlation logic below.
    """

    serializer_class = IrBreakEventSerializer
    event_kind = "ir_break"

    def handle(self, *, locker: Locker, payload: dict) -> None:
        super().handle(locker=locker, payload=payload)
        if not payload.get("broken"):
            return
        # Correlate with most recent door-state event. If the last one was
        # OPEN (and there's no SECURED since), this break means the user
        # took the asset out.
        last_state = (
            LockerAccessEvent.objects.filter(
                locker=locker,
                kind__in=(
                    LockerAccessEventKind.OPEN,
                    LockerAccessEventKind.SECURED,
                ),
            )
            .order_by("-occurred_at")
            .first()
        )
        if last_state is None or last_state.kind != LockerAccessEventKind.OPEN:
            # No matching open state — just log the break itself.
            return
        LockerAccessEvent.objects.create(
            locker=locker,
            kind=LockerAccessEventKind.ITEM_REMOVED,
            asset=locker.current_asset,
            metadata=redact(payload),
        )
        # Asset is leaving the locker; clear the pointer.
        if locker.current_asset_id is not None:
            Locker.objects.filter(pk=locker.pk).update(current_asset=None)


class ReedStatusEventView(_WebhookBase):
    """Persist OPEN on door-opens; SECURED on closes. On a high-trust locker,
    a SECURED transition with a non-null current_asset spawns a pending
    LockerHighTrustReturn that blocks new OTPs until accepted/rejected.
    """

    serializer_class = ReedStatusEventSerializer
    event_kind = "reed_status"

    def handle(self, *, locker: Locker, payload: dict) -> None:
        super().handle(locker=locker, payload=payload)
        kind = LockerAccessEventKind.SECURED if payload["closed"] else LockerAccessEventKind.OPEN
        event = LockerAccessEvent.objects.create(
            locker=locker,
            kind=kind,
            asset=locker.current_asset,
            metadata=redact(payload),
        )
        if (
            kind == LockerAccessEventKind.SECURED
            and locker.is_high_trust
            and locker.current_asset_id is not None
        ):
            # Find the user from the most recent UNLOCKED event so the
            # return is pinned to the right person.
            last_unlock = (
                LockerAccessEvent.objects.filter(
                    locker=locker,
                    kind=LockerAccessEventKind.UNLOCKED,
                )
                .order_by("-occurred_at")
                .first()
            )
            returning_user = last_unlock.actor if last_unlock else None
            if returning_user is not None:
                LockerHighTrustReturn.objects.get_or_create(
                    locker=locker,
                    status=LockerHighTrustReturn.STATUS_PENDING,
                    defaults={
                        "returning_user": returning_user,
                        "asset": locker.current_asset,
                        "triggering_event": event,
                    },
                )


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
