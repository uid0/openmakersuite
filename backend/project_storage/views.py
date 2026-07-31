"""Project Storage API.

Self-service create (member at a kiosk) is intentionally AllowAny — there's
no Django auth at the kiosk. The warden surfaces (history lookup, send
notice, move to purgatory, mark removed) require staff auth.

Staff-assigned storage (committee / logistics / class) is a separate
lifecycle on the same racking: :class:`StorageAssignmentViewSet` hands a slot
out and takes it back, with no kiosk and no clock. The two kinds of occupancy
exclude each other per slot; the guards for that live in
:func:`_reject_if_slot_held`.
"""

from __future__ import annotations

import logging

from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from config.idempotency import find_recent_duplicate
from fiducials.services.allocator import (
    AprilTagPoolExhausted,
    active_tag_id_subquery,
    allocate_tag,
    release_tag,
)

from .models import ProjectStorageEvent, ProjectStorageStint, StorageAssignment, StorageSlot
from .permissions import IsStorageAdminOrStaff
from .serializers import (
    AssignSlotSerializer,
    GenerateRackSerializer,
    ProjectStorageStintSerializer,
    SlotCardBatchSerializer,
    StartStintSerializer,
    StorageAssignmentSerializer,
    StorageOverviewSerializer,
    StorageSlotSerializer,
)
from .services.email_service import send_violation_notice
from .services.label_service import PrinterFamily, render_stint_label
from .services.slot_cards import build_slot_card_preview, render_slot_cards
from .services.storage_overview import build_overview
from .services.storage_slots import LevelSpec, ensure_slot_tag, generate_rack_slots

logger = logging.getLogger(__name__)


def _parse_bool(value, *, default=None):
    """Coerce a query-param string to bool. Returns ``default`` when absent.

    Explicit rather than ``bool(value)`` — the string "false" is truthy.
    """
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


#: What to tell a member standing at the kiosk whose slot is already taken.
#: Callers that aren't the kiosk (the delete guard) pass their own advice —
#: the sentence naming the occupant is built by the helpers below either way,
#: so no caller has to reconstruct it to say something different afterwards.
_KIOSK_PICK_ANOTHER = "Pick a free slot or ask the storage warden to clear that one."


def _slot_occupied_response(slot, occupant, *, advice: str = "") -> Response:
    """The 409 for "a live stint is already in that slot".

    Shared by the claim pre-check, the lost-a-race path, and the delete
    guard so the three can't drift into reporting one condition three
    different ways.
    """
    return Response(
        {
            "detail": (
                f"Slot {slot.code} is already holding stint {occupant.stint_id} "
                f"({occupant.display_name}). {advice or _KIOSK_PICK_ANOTHER}"
            ),
            "code": "slot_occupied",
            "slot_code": slot.code,
            "occupied_by": occupant.stint_id,
        },
        status=status.HTTP_409_CONFLICT,
    )


def _slot_assigned_response(slot, assignment, *, advice: str = "") -> Response:
    """The 409 for "staff already gave that slot to a committee/class/crew".

    Distinct code from ``slot_occupied`` because the remedy is different: a
    member's stint expires and can be cleared by the warden on a sweep, but
    an assignment ends only when staff hands the slot back, and telling a
    member at the kiosk to "wait for it to expire" would be a lie.
    """
    return Response(
        {
            "detail": (
                f"Slot {slot.code} is assigned to {assignment.occupant_display} "
                f"({assignment.get_storage_type_display()}). {advice or 'Pick a free slot.'}"
            ),
            "code": "slot_assigned",
            "slot_code": slot.code,
            "assigned_to": assignment.occupant_display,
            "storage_type": assignment.storage_type,
        },
        status=status.HTTP_409_CONFLICT,
    )


def _reject_if_slot_held(slot, *, advice: str = ""):
    """Return the 409 for whichever live occupancy holds ``slot``, or None.

    One live occupancy per slot, and there are two tables it can be in — so
    no single DB constraint covers it and every write path that hands a slot
    to somebody has to ask both. Claiming, assigning and deleting share this
    so the three can't drift into enforcing different halves of the rule.
    """
    occupant = ProjectStorageStint.active_stint_in_slot(slot)
    if occupant is not None:
        return _slot_occupied_response(slot, occupant, advice=advice)
    assignment = StorageAssignment.active_assignment_in_slot(slot)
    if assignment is not None:
        return _slot_assigned_response(slot, assignment, advice=advice)
    return None


def _slot_is_held() -> Q:
    """Q matching slots with any live occupancy — a stint *or* an assignment.

    ``Exists`` on both sides rather than ``pk__in=…values("slot")``: a
    slot-less stint puts a NULL in that subquery and ``NOT IN (NULL, …)`` is
    UNKNOWN for every row, so the "which slots are free?" half would silently
    return nothing.
    """
    return Q(Exists(ProjectStorageStint.active().filter(slot=OuterRef("pk")))) | Q(
        Exists(StorageAssignment.active().filter(slot=OuterRef("pk")))
    )


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
    # select_related("slot") so the serializer's slot_code / location_display
    # (model properties that walk the FK) don't fire a query per row.
    queryset = ProjectStorageStint.objects.all().select_related("slot").prefetch_related("events")
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

        Scanning a slot's marker claims it outright — there is no warden
        approval step. Name the slot with ``slot`` (its pk or its code) or
        ``slot_code`` (``1A1``, what the scanner reads); the slot then *is*
        the stint's location, and ``storage_location_name`` is left for the
        ad-hoc, non-rack case.

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
        slot = ser.validated_data.get("slot")

        # gh-714 idempotency: a duplicate submit within 5 minutes
        # (network blip → kiosk retry) should resolve to the existing
        # stint, not surface as a 409 error to the member. The slot is
        # part of "the same submission" and this check runs BEFORE the
        # occupancy guard — otherwise a retry of the scan that claimed
        # 1A1 would bounce off its own stint as slot_occupied.
        existing = find_recent_duplicate(
            ProjectStorageStint,
            lookup_fields={
                "username": username,
                "project_title": ser.validated_data.get("project_title", ""),
                "storage_location_name": ser.validated_data.get("storage_location_name", ""),
                "slot": slot,
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

        # One live occupancy per slot, of either kind. For a stint the DB
        # constraint is the real guard (see below) and this pre-check exists
        # so the common case reports who is in the slot instead of a bare
        # integrity failure; for an assignment it *is* the guard, since the
        # two live in different tables.
        held = _reject_if_slot_held(slot)
        if held is not None:
            return held

        try:
            with transaction.atomic():
                stint = ProjectStorageStint.objects.create(
                    username=username,
                    first_name=ser.validated_data.get("first_name", ""),
                    last_name=ser.validated_data.get("last_name", ""),
                    email=ser.validated_data.get("email", ""),
                    project_title=ser.validated_data.get("project_title", ""),
                    storage_location_name=ser.validated_data.get("storage_location_name", ""),
                    slot=slot,
                )
        except IntegrityError:
            # Two members scanned the same slot at the same time and the
            # partial unique constraint picked a winner. Report the loser's
            # request as the 409 the pre-check would have given it. Any
            # other integrity failure is a real bug — let it propagate.
            occupant = ProjectStorageStint.active_stint_in_slot(slot)
            if occupant is None:
                raise
            return _slot_occupied_response(slot, occupant)

        ProjectStorageEvent.objects.create(
            stint=stint,
            event_type=ProjectStorageEvent.EVENT_CREATED,
            actor_label="kiosk: member self-issue",
            note=f"slot {slot.code}" if slot is not None else "",
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
            .select_related("slot")
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
        """Warden physically relocates the items out of project storage.

        The stint keeps its ``slot`` as the historical record of where the
        items were — the racking slot is freed by this transition rather
        than by clearing the FK, because ``unique_active_stint_per_slot``
        only counts unresolved stints. So 1A1 becomes claimable again the
        moment its occupant is purgatoried, and the audit trail still says
        which slot the items came out of.
        """
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
        # Name the vacated slot in the audit note — "moved to Project
        # Purgatory" alone loses the one fact a warden retracing the sweep
        # needs, since the slot is claimable by someone else immediately.
        origin = f"vacated {stint.slot_code}" if stint.slot_id else ""
        ProjectStorageEvent.objects.create(
            stint=stint,
            event_type=ProjectStorageEvent.EVENT_MOVED_TO_PURGATORY,
            actor=request.user if request.user.is_authenticated else None,
            note=" · ".join(part for part in (location, origin) if part),
        )
        return Response(ProjectStorageStintSerializer(stint).data)

    @action(
        detail=True, methods=["post"], url_path="mark-removed", permission_classes=[IsAdminUser]
    )
    def mark_removed(self, request, stint_id: str):
        """Member cleared their stuff out; the stint is done.

        Like ``move_to_purgatory`` this frees the racking slot for the next
        claim without clearing ``stint.slot`` — the partial unique
        constraint stops counting a removed stint, so the history of who
        was in 1A1 survives.
        """
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
        # select_related: the ticket prints the slot code, so the renderer
        # walks the FK on every daemon poll.
        stint = get_object_or_404(
            ProjectStorageStint.objects.select_related("slot"), stint_id=stint_id
        )
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


class StorageSlotViewSet(viewsets.ModelViewSet):
    """CRUD for the physical racking, plus a per-rack bulk generator.

    Addressed by ``code`` (``/slots/1A1/``) rather than PK: the code is what
    is printed on the rack and what a scanner reads, so it's the identifier
    every caller already has.

    Gated to ``IsStorageAdminOrStaff`` throughout — the storage warden owns
    the racking layout, and the group membership exists precisely so that
    duty can be delegated without platform-wide staff rights. The permission
    is declared once at class level (and verbatim on the @action) so the
    permission-matrix gate snapshots it without re-deriving get_permissions().
    """

    queryset = StorageSlot.objects.all().select_related("owning_group")
    serializer_class = StorageSlotSerializer
    lookup_field = "code"
    permission_classes = [IsStorageAdminOrStaff]

    def get_queryset(self):
        """Apply ?rack= / ?level= / ?is_active= / ?requires_pallet_jack= /
        ?occupied=.

        Hand-rolled rather than django-filter, which isn't installed here —
        ``filterset_fields`` would be silently inert.

        ``?occupied=false`` is the warden's "what can I hand out?" query and
        ``?occupied=true`` its "what's on the rack right now?" mirror.
        """
        qs = super().get_queryset()
        params = self.request.query_params if hasattr(self, "request") else {}

        rack = params.get("rack")
        if rack:
            try:
                qs = qs.filter(rack=int(rack))
            except (TypeError, ValueError):
                # A junk rack matches nothing rather than 500ing the list.
                qs = qs.none()

        level = params.get("level")
        if level:
            qs = qs.filter(level=level.upper())

        is_active = _parse_bool(params.get("is_active"))
        if is_active is not None:
            qs = qs.filter(is_active=is_active)

        pallet_jack = _parse_bool(params.get("requires_pallet_jack"))
        if pallet_jack is not None:
            qs = qs.filter(requires_pallet_jack=pallet_jack)

        occupied = _parse_bool(params.get("occupied"))
        if occupied is not None:
            # Held by *either* kind of occupancy: a slot the welding SIG has
            # been assigned is no more available to hand out than one with a
            # member's project in it.
            held = _slot_is_held()
            qs = qs.filter(held) if occupied else qs.filter(~held)

        # Annotate the permanent marker and attach both live occupancies so
        # the serializer resolves them without firing a query per row.
        return (
            qs.annotate(active_april_tag_id=active_tag_id_subquery(StorageSlot))
            .prefetch_related(StorageSlot.active_stints_prefetch())
            .prefetch_related(StorageSlot.active_assignments_prefetch())
        )

    def perform_create(self, serializer):
        """Individually-created slots get their permanent tag immediately.

        DRF reads ``serializer.data`` after this returns, and a freshly
        created instance carries no ``active_april_tag_id`` annotation, so
        the serializer falls back to a direct lookup and the response
        reports the tag just allocated.
        """
        ensure_slot_tag(serializer.save())

    def destroy(self, request, *args, **kwargs):
        """Refuse to delete a slot somebody's stuff is sitting in.

        ``ProjectStorageStint.slot`` is SET_NULL, so without this the
        delete would succeed and quietly strip the location off a live
        stint — the member's stuff is still physically on the rack and now
        nothing records where. ``StorageAssignment.slot`` is CASCADE, which
        is worse: the record of the committee holding the slot would be
        deleted outright. Resolve the stint first (mark-removed /
        move-to-purgatory) or release the assignment, or deactivate the slot
        instead.
        """
        instance = self.get_object()
        held = _reject_if_slot_held(
            instance,
            advice=(
                "Resolve it first, or set is_active=false to retire the slot "
                "without deleting it."
            ),
        )
        if held is not None:
            return held
        # Mirrors DRF's own destroy(); inlined so the guard above doesn't
        # cost a second fetch of the same row.
        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def perform_destroy(self, instance):
        """Release the marker before the row goes away.

        Slot tags are never released while the slot *exists* — a code has to
        keep meaning the same physical place. A hard delete is the one case
        where that promise ends: leaving the assignment behind would both
        strand a pool ID forever and leave a dangling GenericForeignKey
        (there is no DB-level cascade), so a scan of that ID would resolve
        to nothing while still reading as taken. Deactivating (is_active =
        False) is the non-destructive option and keeps the tag.
        """
        release_tag(instance)
        instance.delete()

    @action(
        detail=False,
        methods=["post"],
        url_path="generate",
        permission_classes=[IsStorageAdminOrStaff],
    )
    def generate(self, request):
        """Bulk-create one rack's slots from a per-level spec. Idempotent.

        Payload::

            {"rack": 1,
             "levels": [{"level": "A", "positions": 12},
                        {"level": "Y", "positions": 10,
                         "requires_pallet_jack": true}],
             "owning_group": 3}          # optional

        Existing codes are left untouched and reported under ``skipped``, so
        re-running after adding a level (or after a partial failure) is safe.
        """
        ser = GenerateRackSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        result = generate_rack_slots(
            rack=ser.validated_data["rack"],
            levels=[
                LevelSpec(
                    level=spec["level"],
                    positions=spec["positions"],
                    requires_pallet_jack=spec.get("requires_pallet_jack", False),
                )
                for spec in ser.validated_data["levels"]
            ],
            owning_group=ser.validated_data.get("owning_group"),
            notes=ser.validated_data.get("notes", ""),
        )

        # Built from the base manager, not get_queryset(), so a stray query
        # string on the POST can't filter rows out of the run's own report.
        slots = (
            StorageSlot.objects.filter(code__in=[slot.code for slot in result.slots])
            .select_related("owning_group")
            .annotate(active_april_tag_id=active_tag_id_subquery(StorageSlot))
            .prefetch_related(
                StorageSlot.active_stints_prefetch(),
                StorageSlot.active_assignments_prefetch(),
            )
            .order_by("rack", "level", "position")
        )
        payload = {
            "rack": result.rack,
            "created": [slot.code for slot in result.created],
            "skipped": [slot.code for slot in result.skipped],
            "created_count": len(result.created),
            "skipped_count": len(result.skipped),
            # Non-empty means the tag family ran dry mid-run; the slots are
            # usable by code, they just have no marker to scan yet.
            "without_tag": [slot.code for slot in result.without_tag],
            "slots": StorageSlotSerializer(slots, many=True).data,
        }
        return Response(
            payload,
            status=status.HTTP_201_CREATED if result.created else status.HTTP_200_OK,
        )

    # ------------------------------------------------------------------
    # Printable cards (Avery 5388, 3 per sheet)
    # ------------------------------------------------------------------

    @action(
        detail=True,
        methods=["get"],
        url_path="card-preview",
        permission_classes=[IsStorageAdminOrStaff],
    )
    def card_preview(self, request, code: str):
        """Base64 PDF of this one slot's card, for an on-screen preview.

        Mirrors the index-card preview endpoint: a single-card PDF, encoded
        so the caller can drop it straight into an ``<iframe>`` without a
        second round trip. Also reports the kiosk URL and marker ID the card
        carries so the preview surface can show what was encoded.
        """
        return Response(build_slot_card_preview(self.get_object()))

    @action(
        detail=False,
        methods=["post"],
        url_path="cards",
        permission_classes=[IsStorageAdminOrStaff],
    )
    def cards(self, request):
        """Batch-render slot cards as a multi-page 5388 PDF (3 cards/page).

        Payload is either ``{"slot_ids": [...]}`` or a rack filter
        (``{"rack": 1, "level": "A", "include_inactive": false}``) — see
        :class:`SlotCardBatchSerializer`. Explicit IDs print in the order
        they were given (reprinting a handful of cards keeps the caller's
        order); a rack prints in code order, which is the order the sheets
        get stuck on the uprights.

        Returns the PDF itself rather than a stored file: these sheets are
        printed once and thrown away, so persisting them would only grow
        MEDIA_ROOT.
        """
        ser = SlotCardBatchSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        # Base manager + the tag annotation: one query for the whole sheet
        # instead of a marker lookup per card. Not get_queryset(), so a stray
        # query string on the POST can't silently drop cards from the run.
        base = StorageSlot.objects.select_related("owning_group").annotate(
            active_april_tag_id=active_tag_id_subquery(StorageSlot)
        )

        slot_ids = ser.validated_data.get("slot_ids")
        if slot_ids:
            found = {slot.pk: slot for slot in base.filter(pk__in=slot_ids)}
            missing = [slot_id for slot_id in slot_ids if slot_id not in found]
            if missing:
                return Response(
                    {
                        "detail": "Some requested storage slots were not found.",
                        "missing_ids": missing,
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )
            # De-duplicated, but otherwise exactly the order asked for.
            slots = []
            seen: set[int] = set()
            for slot_id in slot_ids:
                if slot_id in seen:
                    continue
                seen.add(slot_id)
                slots.append(found[slot_id])
            filename = "storage_slot_cards.pdf"
        else:
            rack = ser.validated_data["rack"]
            level = ser.validated_data.get("level")
            qs = base.filter(rack=rack).order_by("rack", "level", "position")
            if level:
                qs = qs.filter(level=level)
            if not ser.validated_data.get("include_inactive"):
                qs = qs.filter(is_active=True)
            slots = list(qs)
            if not slots:
                return Response(
                    {
                        "detail": f"No storage slots match rack {rack}"
                        + (f", level {level}" if level else "")
                        + ".",
                        "code": "no_slots_matched",
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )
            filename = f"storage_slot_cards_rack{rack}{level or ''}.pdf"

        pdf_bytes = render_slot_cards(slots)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class StorageAssignmentViewSet(viewsets.ReadOnlyModelViewSet):
    """Staff-assigned storage: committee (C), logistics (L), class (E).

    The long-term half of the racking. Where a member claims a slot at the
    kiosk and a 30-day clock starts, these slots are handed out by staff and
    stay handed out — no expiry, no violation notice, no purgatory, no
    cool-down. The whole lifecycle is ``assign`` → ``release``, both gated to
    ``IsStorageAdminOrStaff`` like the racking itself, so the storage warden
    can run it without platform-wide staff rights.

    Read-only for CRUD on purpose: the two actions are the only way in and
    out, which keeps ``assigned_by`` meaningful and stops a PATCH from
    quietly reopening a released assignment.
    """

    queryset = StorageAssignment.objects.all().select_related("slot", "owning_group", "assigned_by")
    serializer_class = StorageAssignmentSerializer
    permission_classes = [IsStorageAdminOrStaff]

    def get_queryset(self):
        """Apply ?active= / ?storage_type= / ?rack= / ?slot_code=.

        Hand-rolled rather than django-filter, which isn't installed here —
        ``filterset_fields`` would be silently inert.

        ``?active=true`` is the one the staff console lives on ("who holds
        what right now?"); the unfiltered list is the history of every
        holding the racking has ever had.
        """
        qs = super().get_queryset()
        params = self.request.query_params if hasattr(self, "request") else {}

        active = _parse_bool(params.get("active"))
        if active is not None:
            qs = qs.filter(released_at__isnull=active)

        storage_type = params.get("storage_type")
        if storage_type:
            qs = qs.filter(storage_type=storage_type)

        rack = params.get("rack")
        if rack:
            try:
                qs = qs.filter(slot__rack=int(rack))
            except (TypeError, ValueError):
                # A junk rack matches nothing rather than 500ing the list.
                qs = qs.none()

        slot_code = params.get("slot_code")
        if slot_code:
            qs = qs.filter(slot__code=slot_code.strip().upper())

        return qs

    @action(
        detail=False,
        methods=["post"],
        permission_classes=[IsStorageAdminOrStaff],
    )
    def assign(self, request):
        """Hand a slot to a committee, the logistics crew, or a class.

        Payload::

            {"slot": "1A1",                   # or the pk; slot_code also works
             "storage_type": "committee",
             "owning_group": 3,               # the SIG, for committee
             "occupant_label": "Ana's CNC class",   # free text, for L/E
             "notes": ""}

        Refuses a slot that already holds anything — a member's live stint
        (409 ``slot_occupied``) or another assignment (409 ``slot_assigned``).
        """
        ser = AssignSlotSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        slot = ser.validated_data["slot"]

        held = _reject_if_slot_held(
            slot,
            advice="Resolve or release it before assigning the slot.",
        )
        if held is not None:
            return held

        try:
            with transaction.atomic():
                assignment = StorageAssignment.objects.create(
                    slot=slot,
                    storage_type=ser.validated_data["storage_type"],
                    owning_group=ser.validated_data.get("owning_group"),
                    occupant_label=ser.validated_data.get("occupant_label", ""),
                    notes=ser.validated_data.get("notes", ""),
                    assigned_by=request.user if request.user.is_authenticated else None,
                )
        except IntegrityError:
            # Two wardens assigned the same slot at once and
            # unique_active_assignment_per_slot picked a winner. Report the
            # loser's request as the 409 the pre-check would have given it;
            # any other integrity failure is a real bug, so re-raise.
            existing = StorageAssignment.active_assignment_in_slot(slot)
            if existing is None:
                raise
            return _slot_assigned_response(slot, existing)

        return Response(
            StorageAssignmentSerializer(assignment).data,
            status=status.HTTP_201_CREATED,
        )

    @action(
        detail=True,
        methods=["post"],
        permission_classes=[IsStorageAdminOrStaff],
    )
    def release(self, request, pk: str = None):
        """Take the slot back. The row stays as the history of who had it.

        Like resolving a stint, this frees the slot by ceasing to match the
        partial unique constraint rather than by deleting anything — 1A1 is
        immediately assignable (or claimable at the kiosk) again, and the
        record of the two years the welding SIG held it survives.
        """
        assignment = self.get_object()
        if assignment.released_at is not None:
            return Response(
                {
                    "detail": (
                        f"Slot {assignment.slot.code} was already released on "
                        f"{assignment.released_at:%Y-%m-%d}."
                    ),
                    "code": "already_released",
                },
                status=status.HTTP_409_CONFLICT,
            )
        assignment.released_at = timezone.now()
        assignment.save(update_fields=["released_at", "updated_at"])
        return Response(StorageAssignmentSerializer(assignment).data)


class StorageOverviewView(APIView):
    """``GET /api/project-storage/overview/`` — the per-rack status grid.

    The read Ian actually does: pull it up on a phone, look for the coloured
    cells, go fix those. ``?rack=1`` narrows to one rack for a small screen.

    Staff-gated to the same ``IsStorageAdminOrStaff`` as the racking it
    describes — it names every member with something on the shelves.

    The payload's shape and its type/status/colour rules are documented in
    ``services/storage_overview.py``; this view is just the HTTP door.
    """

    permission_classes = [IsStorageAdminOrStaff]
    # Declared for the OpenAPI schema; the response is rendered through it
    # below, so the documented shape is the shape that ships.
    serializer_class = StorageOverviewSerializer

    def get(self, request):
        payload = build_overview(rack=request.query_params.get("rack"))
        return Response(StorageOverviewSerializer(payload).data)
