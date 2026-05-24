"""Project Storage API.

Self-service create (member at a kiosk) is intentionally AllowAny — there's
no Django auth at the kiosk. The warden surfaces (history lookup, send
notice, move to purgatory, mark removed) require staff auth.
"""

from __future__ import annotations

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response

from .models import ProjectStorageEvent, ProjectStorageStint
from .serializers import (
    ProjectStorageStintSerializer,
    StartStintSerializer,
)
from .services.email_service import send_violation_notice
from .services.label_service import PrinterFamily, render_stint_label


class ProjectStorageStintViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ProjectStorageStint.objects.all().prefetch_related("events")
    serializer_class = ProjectStorageStintSerializer
    lookup_field = "stint_id"

    def get_permissions(self):
        # AllowAny on the kiosk + Pi-daemon paths; staff for everything
        # else. The decorators below also set permission_classes so the
        # routing is obvious at the call site, but this central list is
        # the safety net for actions reached through self.get_object().
        if self.action in ("start", "label", "print_queue", "mark_printed"):
            return [AllowAny()]
        return [IsAdminUser()]

    # ------------------------------------------------------------------
    # Self-service: member at a kiosk
    # ------------------------------------------------------------------

    @action(detail=False, methods=["post"], permission_classes=[AllowAny])
    def start(self, request):
        """Member-initiated kiosk flow: create a stint + return label payload."""
        ser = StartStintSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        username = ser.validated_data["username"]

        if ProjectStorageStint.member_has_active_stint(username):
            return Response(
                {
                    "detail": (
                        "You already have an active project-storage stint. "
                        "Resolve it with the storage warden before starting "
                        "a new one."
                    ),
                    "code": "active_stint_exists",
                },
                status=status.HTTP_409_CONFLICT,
            )

        unblock = ProjectStorageStint.cooldown_blocks_new_stint(username)
        if unblock is not None:
            return Response(
                {
                    "detail": (
                        "A 3-day cool-down is required between project-storage "
                        f"stints. You can start a new stint after {unblock:%Y-%m-%d %H:%M %Z}."
                    ),
                    "code": "cooldown_active",
                    "cooldown_until": unblock,
                },
                status=status.HTTP_409_CONFLICT,
            )

        stint = ProjectStorageStint.objects.create(
            username=username,
            first_name=ser.validated_data.get("first_name", ""),
            last_name=ser.validated_data.get("last_name", ""),
            email=ser.validated_data.get("email", ""),
            project_title=ser.validated_data.get("project_title", ""),
            storage_location_name=ser.validated_data.get("storage_location_name", ""),
        )
        ProjectStorageEvent.objects.create(
            stint=stint,
            event_type=ProjectStorageEvent.EVENT_CREATED,
            actor_label="kiosk: member self-issue",
        )
        return Response(
            ProjectStorageStintSerializer(stint).data,
            status=status.HTTP_201_CREATED,
        )

    # ------------------------------------------------------------------
    # Warden lookups
    # ------------------------------------------------------------------

    @action(
        detail=False,
        methods=["get"],
        url_path=r"by-member/(?P<username>[^/.]+)",
    )
    def by_member(self, request, username: str):
        """All stints (most recent first) for one member."""
        stints = (
            ProjectStorageStint.objects.filter(username=username)
            .prefetch_related("events")
            .order_by("-started_at")
        )
        return Response(ProjectStorageStintSerializer(stints, many=True).data)

    # ------------------------------------------------------------------
    # Warden mutating actions
    # ------------------------------------------------------------------

    @action(detail=True, methods=["post"], url_path="send-violation-notice")
    def send_violation_notice(self, request, stint_id: str):
        stint = self.get_object()
        if stint.compute_status() not in (
            ProjectStorageStint.STATUS_EXPIRED,
            ProjectStorageStint.STATUS_EXPIRING_SOON,
            ProjectStorageStint.STATUS_PURGATORY_WARNED,
        ):
            return Response(
                {
                    "detail": (
                        "Violation notice is only valid for expiring/expired/"
                        "already-warned stints."
                    ),
                    "code": "invalid_state_for_notice",
                },
                status=status.HTTP_409_CONFLICT,
            )
        sent = send_violation_notice(stint)
        if not sent:
            return Response(
                {
                    "detail": (
                        "Member has no on-file email; record the violation but "
                        "the system can't send the notice."
                    ),
                    "code": "missing_email",
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        stint.notice_sent_at = timezone.now()
        stint.save(update_fields=["notice_sent_at", "updated_at"])
        ProjectStorageEvent.objects.create(
            stint=stint,
            event_type=ProjectStorageEvent.EVENT_NOTICE_SENT,
            actor=request.user if request.user.is_authenticated else None,
        )
        return Response(ProjectStorageStintSerializer(stint).data)

    @action(detail=True, methods=["post"], url_path="move-to-purgatory")
    def move_to_purgatory(self, request, stint_id: str):
        stint = self.get_object()
        if stint.notice_sent_at is None:
            return Response(
                {
                    "detail": (
                        "Send the violation notice first — purgatory requires "
                        "a 7-day notice period."
                    ),
                    "code": "notice_required",
                },
                status=status.HTTP_409_CONFLICT,
            )
        location = request.data.get("purgatory_location_name", "")
        if location:
            stint.purgatory_location_name = location
        stint.moved_to_purgatory_at = timezone.now()
        stint.save(
            update_fields=[
                "moved_to_purgatory_at",
                "purgatory_location_name",
                "updated_at",
            ]
        )
        ProjectStorageEvent.objects.create(
            stint=stint,
            event_type=ProjectStorageEvent.EVENT_MOVED_TO_PURGATORY,
            actor=request.user if request.user.is_authenticated else None,
            note=location,
        )
        return Response(ProjectStorageStintSerializer(stint).data)

    @action(detail=True, methods=["post"], url_path="mark-removed")
    def mark_removed(self, request, stint_id: str):
        stint = self.get_object()
        if stint.removed_at is not None:
            return Response(
                {"detail": "Stint already marked removed.", "code": "already_removed"},
                status=status.HTTP_409_CONFLICT,
            )
        stint.removed_at = timezone.now()
        stint.save(update_fields=["removed_at", "updated_at"])
        ProjectStorageEvent.objects.create(
            stint=stint,
            event_type=ProjectStorageEvent.EVENT_REMOVED,
            actor=request.user if request.user.is_authenticated else None,
            note=request.data.get("note", ""),
        )
        return Response(ProjectStorageStintSerializer(stint).data)

    # ------------------------------------------------------------------
    # Label rendering — the Pi print daemon pulls this
    # ------------------------------------------------------------------

    @action(
        detail=True,
        methods=["get"],
        url_path="label",
        permission_classes=[AllowAny],
    )
    def label(self, request, stint_id: str):
        """Return the label PNG. ?printer=brother_ql (default) or epson_tm."""
        stint = get_object_or_404(ProjectStorageStint, stint_id=stint_id)
        printer: PrinterFamily = request.query_params.get(
            "printer", stint.print_target or "brother_ql"
        )
        if printer not in ("brother_ql", "epson_tm"):
            return Response(
                {"detail": f"Unknown printer family '{printer}'.", "code": "bad_printer"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        png_bytes = render_stint_label(stint, printer=printer)
        return HttpResponse(png_bytes, content_type="image/png")

    # ------------------------------------------------------------------
    # Pi print-daemon plumbing
    # ------------------------------------------------------------------

    @action(
        detail=False,
        methods=["get"],
        url_path="print-queue",
        permission_classes=[AllowAny],
    )
    def print_queue(self, request):
        """Return the list of stints that still need their label printed.

        AllowAny because the daemon talks to the API without Django auth;
        the only thing it can do with the queue is print a label (which
        is itself an AllowAny endpoint). If the daemon ever needs to
        mutate something more sensitive, add token auth.
        """
        pending = (
            ProjectStorageStint.objects.filter(
                printed_at__isnull=True,
                removed_at__isnull=True,
                moved_to_purgatory_at__isnull=True,
            )
            .order_by("created_at")
            .values(
                "stint_id",
                "print_target",
                "created_at",
            )
        )
        # Materialize + add the absolute label URL so the daemon doesn't
        # have to know about path construction.
        base = request.build_absolute_uri("/api/project-storage/stints/")
        return Response(
            [
                {
                    "stint_id": row["stint_id"],
                    "print_target": row["print_target"] or "brother_ql",
                    "created_at": row["created_at"],
                    "label_url": (
                        f"{base}{row['stint_id']}/label/"
                        f"?printer={row['print_target'] or 'brother_ql'}"
                    ),
                }
                for row in pending
            ]
        )

    @action(
        detail=True,
        methods=["post"],
        url_path="mark-printed",
        permission_classes=[AllowAny],
    )
    def mark_printed(self, request, stint_id: str):
        """Daemon calls this after a successful print so the queue empties."""
        stint = self.get_object()
        if stint.printed_at is None:
            stint.printed_at = timezone.now()
            stint.save(update_fields=["printed_at", "updated_at"])
            ProjectStorageEvent.objects.create(
                stint=stint,
                event_type=ProjectStorageEvent.EVENT_NOTE_ADDED,
                actor_label="pi-daemon: printed",
                note=request.data.get("note", ""),
            )
        return Response(ProjectStorageStintSerializer(stint).data)
