"""Project Storage API.

Self-service create (member at a kiosk) is intentionally AllowAny — there's
no Django auth at the kiosk. The warden surfaces (history lookup, send
notice, move to purgatory, mark removed) require staff auth.
"""

from __future__ import annotations

import logging

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from config.idempotency import find_recent_duplicate
from fiducials.services.allocator import (
    AprilTagPoolExhausted,
    active_tag_id_subquery,
    allocate_tag,
    release_tag,
)

from .models import ProjectStorageEvent, ProjectStorageStint
from .permissions import IsStorageAdminOrStaff
from .serializers import ProjectStorageStintSerializer, StartStintSerializer
from .services.email_service import send_violation_notice
from .services.label_service import PrinterFamily, render_stint_label

logger = logging.getLogger(__name__)


def _allocate_where_fiducial(stint) -> None:
    """Allocate the stint's AprilTag (WHERE fiducial), degrading rather than
    blocking the kiosk flow if the global tag pool is exhausted.

    The tag36h11 pool is finite (587 IDs); if it's full we still create the
    stint and print its QR (WHO link) — we just log loud so an operator can
    recycle IDs or bump to a larger family. allocate_tag is idempotent, so
    the duplicate-submit path that returns an existing stint is a no-op.
    """
    try:
        allocate_tag(stint)
    except AprilTagPoolExhausted:
        logger.error(
            "AprilTag pool exhausted; stint %s created without a WHERE fiducial.",
            stint.stint_id,
        )


# gh-713 throttle subclasses. ScopedRateThrottle.allow_request normally
# reads ``view.throttle_scope`` and returns True (no throttling) when
# the view doesn't expose one. For per-action throttling we want the
# scope baked into the throttle CLASS itself, so each subclass below
# overrides allow_request to use its own ``scope`` attribute and skip
# the view-attribute lookup. Rates still come from
# DEFAULT_THROTTLE_RATES so all tuning stays in settings.py.


class _BakedScopedThrottle(ScopedRateThrottle):
    scope: str = ""

    def allow_request(self, request, view):
        self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)
        return super(ScopedRateThrottle, self).allow_request(request, view)


class _ProjectStorageStartThrottle(_BakedScopedThrottle):
    scope = "project_storage_start"


class _PiDaemonThrottle(_BakedScopedThrottle):
    scope = "pi_daemon"


class ProjectStorageStintViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ProjectStorageStint.objects.all().prefetch_related("events")
    serializer_class = ProjectStorageStintSerializer
    lookup_field = "stint_id"
    # Class-level default for list + retrieve (the DRF-built-in actions
    # that have no @action decorator). Each @action overrides this with
    # its own permission_classes, kept verbatim on the decorator so the
    # API permission-matrix gate (config/permission_matrix.py) snapshots
    # them without re-implementing the get_permissions() logic.
    permission_classes = [IsStorageAdminOrStaff]

    # ------------------------------------------------------------------
    # Self-service: member at a kiosk
    # ------------------------------------------------------------------

    @action(
        detail=False,
        methods=["post"],
        permission_classes=[AllowAny],
        throttle_classes=[_ProjectStorageStartThrottle],
    )
    def start(self, request):
        """Member-initiated kiosk flow: create a stint + return label payload.

        Throttled per-IP via the ``project_storage_start`` scope (5/hour).
        A real member starts one stint per ~month; an abuse loop trying to
        spam new stints from a single kiosk should top out fast.

        Idempotent against network retries (gh-714): if the same member
        submitted the same payload within the last 5 minutes, returns the
        existing stint with HTTP 200 instead of failing with 409. The
        "you already have an active stint" 409 still fires when the prior
        stint is older than the window — that's the legitimate "go talk
        to the warden" case.
        """
        ser = StartStintSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        username = ser.validated_data["username"]

        # gh-714 idempotency: a duplicate submit within 5 minutes
        # (network blip → kiosk retry) should resolve to the existing
        # stint, not surface as a 409 error to the member.
        existing = find_recent_duplicate(
            ProjectStorageStint,
            lookup_fields={
                "username": username,
                "project_title": ser.validated_data.get("project_title", ""),
                "storage_location_name": ser.validated_data.get("storage_location_name", ""),
                "removed_at__isnull": True,
                "moved_to_purgatory_at__isnull": True,
            },
            created_at_field="started_at",
        )
        if existing is not None:
            return Response(
                ProjectStorageStintSerializer(existing).data,
                status=status.HTTP_200_OK,
            )

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
        # Allocate the per-item WHERE fiducial so the label prints with it.
        _allocate_where_fiducial(stint)
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
        permission_classes=[IsStorageAdminOrStaff],
    )
    def by_member(self, request, username: str):
        """All stints (most recent first) for one member."""
        stints = (
            ProjectStorageStint.objects.filter(username=username)
            .prefetch_related("events")
            .annotate(active_april_tag_id=active_tag_id_subquery(ProjectStorageStint))
            .order_by("-started_at")
        )
        return Response(ProjectStorageStintSerializer(stints, many=True).data)

    def get_queryset(self):
        """Apply ?status=… and ?ordering=… to the read-only list endpoint.

        compute_status() is Python-side, so we translate each STATUS_*
        bucket into the column predicates that compute_status() uses
        and filter at the DB. Order matters in compute_status (terminal
        wins) so each filter has to exclude the rows a higher-priority
        status would claim.

        Supported orderings: ``expires_at``, ``-expires_at``,
        ``started_at``, ``-started_at``. Anything else falls back to the
        default (-started_at, newest first) so an operator can't tank
        the warden queue by passing a junk field.
        """
        from datetime import timedelta as _td

        from django.utils import timezone as _tz

        from .models import EXPIRING_SOON_WINDOW_DAYS

        qs = super().get_queryset()
        status_filter = (
            self.request.query_params.get("status") if hasattr(self, "request") else None
        )
        if status_filter:
            now = _tz.now()
            soon = now + _td(days=EXPIRING_SOON_WINDOW_DAYS)
            if status_filter == ProjectStorageStint.STATUS_REMOVED:
                qs = qs.filter(removed_at__isnull=False)
            elif status_filter == ProjectStorageStint.STATUS_PURGATORY:
                qs = qs.filter(removed_at__isnull=True, moved_to_purgatory_at__isnull=False)
            elif status_filter == ProjectStorageStint.STATUS_PURGATORY_WARNED:
                qs = qs.filter(
                    removed_at__isnull=True,
                    moved_to_purgatory_at__isnull=True,
                    notice_sent_at__isnull=False,
                )
            elif status_filter == ProjectStorageStint.STATUS_EXPIRED:
                qs = qs.filter(
                    removed_at__isnull=True,
                    moved_to_purgatory_at__isnull=True,
                    notice_sent_at__isnull=True,
                    expires_at__lte=now,
                )
            elif status_filter == ProjectStorageStint.STATUS_EXPIRING_SOON:
                qs = qs.filter(
                    removed_at__isnull=True,
                    moved_to_purgatory_at__isnull=True,
                    notice_sent_at__isnull=True,
                    expires_at__gt=now,
                    expires_at__lte=soon,
                )
            elif status_filter == ProjectStorageStint.STATUS_ACTIVE:
                qs = qs.filter(
                    removed_at__isnull=True,
                    moved_to_purgatory_at__isnull=True,
                    notice_sent_at__isnull=True,
                    expires_at__gt=soon,
                )
            # Unknown status values fall through unchanged — DRF will
            # return whatever the queryset has, the frontend's pill
            # filter only sends known values.

        ordering = self.request.query_params.get("ordering") if hasattr(self, "request") else None
        if ordering in ("expires_at", "-expires_at", "started_at", "-started_at"):
            qs = qs.order_by(ordering)
        else:
            qs = qs.order_by("-started_at")
        # Annotate the active WHERE-fiducial tag id so the serializer's
        # april_tag_id doesn't fire a query per row on the list endpoint.
        return qs.annotate(active_april_tag_id=active_tag_id_subquery(ProjectStorageStint))

    # ------------------------------------------------------------------
    # Warden mutating actions
    # ------------------------------------------------------------------

    @action(
        detail=True,
        methods=["post"],
        url_path="send-violation-notice",
        permission_classes=[IsAdminUser],
    )
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

    @action(
        detail=True,
        methods=["post"],
        url_path="move-to-purgatory",
        permission_classes=[IsAdminUser],
    )
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

    @action(
        detail=True, methods=["post"], url_path="mark-removed", permission_classes=[IsAdminUser]
    )
    def mark_removed(self, request, stint_id: str):
        stint = self.get_object()
        if stint.removed_at is not None:
            return Response(
                {"detail": "Stint already marked removed.", "code": "already_removed"},
                status=status.HTTP_409_CONFLICT,
            )
        stint.removed_at = timezone.now()
        stint.save(update_fields=["removed_at", "updated_at"])
        # Terminal state: free the AprilTag ID back to the pool. We do NOT
        # release on expiry/purgatory — detecting unmoved/expired items is
        # the whole point, so the tag stays live while the item is present.
        release_tag(stint)
        # get_object() fetched this row via the annotated get_queryset(), so
        # its active_april_tag_id is now stale; reflect the freed tag so the
        # serialized response reports april_tag_id = null.
        stint.active_april_tag_id = None
        ProjectStorageEvent.objects.create(
            stint=stint,
            event_type=ProjectStorageEvent.EVENT_REMOVED,
            actor=request.user if request.user.is_authenticated else None,
            note=request.data.get("note", ""),
        )
        return Response(ProjectStorageStintSerializer(stint).data)

    @action(
        detail=True,
        methods=["post"],
        url_path="reprint",
        permission_classes=[IsAdminUser],
    )
    def reprint(self, request, stint_id: str):
        """Warden re-queues a stint's claim ticket for printing.

        The reverse of the daemon's ``mark_printed``: clearing ``printed_at``
        drops the stint back into ``print_queue`` so the Pi daemon reprints
        its label on its next poll. Gated to ``IsAdminUser`` like the other
        warden mutations — the daemon never reprints on its own.

        Unblocks the ScanTTY reprint action (and the web parity button),
        both of which already POST this path.
        """
        stint = self.get_object()
        stint.printed_at = None
        stint.save(update_fields=["printed_at", "updated_at"])
        ProjectStorageEvent.objects.create(
            stint=stint,
            event_type=ProjectStorageEvent.EVENT_NOTE_ADDED,
            actor=request.user if request.user.is_authenticated else None,
            actor_label="reprint requested",
            note=request.data.get("note", ""),
        )
        return Response(ProjectStorageStintSerializer(stint).data)

    @action(
        detail=True,
        methods=["post"],
        url_path="generate-qr",
        permission_classes=[IsStorageAdminOrStaff],
    )
    def generate_qr(self, request, stint_id: str):
        """Regenerate the persisted QR PNG on stint.qr_code.

        Mirrors the inventory regenerate_qr_code endpoints — same
        QRCodeRateLimiter (item_type="project_storage_stint"), same
        logo-embed + pyzbar validation through QRCodeService. The
        rate limit guards against a click-storm dragging Pillow into
        the request path.

        The Pi label-print pipeline doesn't consume this field —
        labels are rendered fresh at print time. This is for the
        warden detail page's "Regenerate QR" affordance and the
        admin preview surface.
        """
        from inventory.services.qr_code_service import QRCodeService
        from inventory.utils.rate_limiting import QRCodeRateLimiter

        stint = self.get_object()

        x_forwarded_for = request.headers.get("x-forwarded-for")
        ip_address = (
            x_forwarded_for.split(",")[0] if x_forwarded_for else request.META.get("REMOTE_ADDR")
        )

        is_allowed, error_msg = QRCodeRateLimiter.check_rate_limit(
            user=request.user if request.user.is_authenticated else None,
            item_id=str(stint.stint_id),
            item_type="project_storage_stint",
            ip_address=ip_address,
        )
        if not is_allowed:
            return Response(
                {"error": error_msg},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        try:
            include_logo = bool(request.data.get("include_logo", True))
            QRCodeService(include_logo=include_logo).generate_for_stint(stint)
        except Exception as exc:  # noqa: BLE001 — surface to the caller
            return Response(
                {"detail": f"QR generation failed: {exc}", "code": "qr_generation_failed"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
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
        throttle_classes=[_PiDaemonThrottle],
    )
    def label(self, request, stint_id: str):
        """Return the label PNG. ?printer=brother_ql (default) or epson_tm.

        Throttled per-IP via the ``pi_daemon`` scope (120/min) — the daemon
        polls every ~10 s and a single IP may serve multiple printers
        behind NAT.
        """
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
        throttle_classes=[_PiDaemonThrottle],
    )
    def print_queue(self, request):
        """Return the list of stints that still need their label printed.

        AllowAny because the daemon talks to the API without Django auth;
        the only thing it can do with the queue is print a label (which
        is itself an AllowAny endpoint). If the daemon ever needs to
        mutate something more sensitive, add token auth.

        Throttled per-IP via the ``pi_daemon`` scope (120/min).
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
        throttle_classes=[_PiDaemonThrottle],
    )
    def mark_printed(self, request, stint_id: str):
        """Daemon calls this after a successful print so the queue empties.

        Throttled per-IP via the ``pi_daemon`` scope (120/min).
        """
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
