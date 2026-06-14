"""API views for the maker box (personal storage bin) system."""

from __future__ import annotations

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from membership.utils import is_logistics_member

from .models import MakerBox
from .serializers import (
    LookupRequestSerializer,
    LookupResponseSerializer,
    MakerBoxSerializer,
    ManualLabelRequestSerializer,
    PreConvertRequestSerializer,
    ScanRequestSerializer,
    ScanResponseSerializer,
)
from .services.email_service import send_pickup_notification
from .services.identity_resolver import resolve as resolve_identity
from .services.label_service import render_box_label
from .services.sheet_service import CARDS_PER_SHEET, render_avery_5371_sheet
from .services.whmcs_client import WhmcsNotConfigured, lookup_member


def _is_staff(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    return user.is_staff or user.is_superuser or is_logistics_member(user)


class MakerBoxViewSet(viewsets.ModelViewSet):
    """CRUD + scan / label / email-pickup actions for personal storage bins."""

    queryset = MakerBox.objects.all()
    serializer_class = MakerBoxSerializer
    permission_classes = [IsAuthenticated]

    def _check_staff(self, request):
        if not _is_staff(request.user):
            return Response(
                {"detail": "Staff (Logistics) access required."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return None

    def list(self, request, *args, **kwargs):
        denied = self._check_staff(request)
        if denied is not None:
            return denied
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        denied = self._check_staff(request)
        if denied is not None:
            return denied
        return super().retrieve(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        denied = self._check_staff(request)
        if denied is not None:
            return denied
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        denied = self._check_staff(request)
        if denied is not None:
            return denied
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        denied = self._check_staff(request)
        if denied is not None:
            return denied
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        denied = self._check_staff(request)
        if denied is not None:
            return denied
        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=["post"], url_path="scan")
    def scan(self, request):
        """Look up a username in WHMCS and update the bin assignment."""
        denied = self._check_staff(request)
        if denied is not None:
            return denied

        serializer = ScanRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        bin_id = serializer.validated_data["bin_id"]
        username = serializer.validated_data["username"]

        try:
            lookup = lookup_member(username)
        except WhmcsNotConfigured as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        bin_obj, _ = MakerBox.objects.get_or_create(bin_id=bin_id)

        if lookup is None:
            bin_obj.assigned_username = username
            bin_obj.status = MakerBox.STATUS_UNKNOWN
            bin_obj.last_verified_at = timezone.now()
            bin_obj.save(
                update_fields=["assigned_username", "status", "last_verified_at", "updated_at"]
            )
            payload = {
                "status": "unknown",
                "bin_id": bin_id,
                "username": username,
                "first_name": "",
                "last_name": "",
                "email": "",
                "expires_at": None,
                "days_remaining": None,
            }
            return Response(ScanResponseSerializer(payload).data)

        bin_obj.assigned_username = username
        bin_obj.first_name = lookup.first_name
        bin_obj.last_name = lookup.last_name
        bin_obj.email = lookup.email
        bin_obj.expires_at = lookup.expires_at
        bin_obj.status = lookup.status
        bin_obj.last_verified_at = timezone.now()
        if not bin_obj.assigned_at and lookup.status in {"valid", "grace"}:
            bin_obj.assigned_at = timezone.now()
        bin_obj.save()

        payload = {
            "status": lookup.status,
            "bin_id": bin_id,
            "username": username,
            "first_name": lookup.first_name,
            "last_name": lookup.last_name,
            "email": lookup.email,
            "expires_at": lookup.expires_at,
            "days_remaining": lookup.days_remaining,
        }
        return Response(ScanResponseSerializer(payload).data)

    @action(detail=True, methods=["get"], url_path="label")
    def label(self, request, pk=None):
        denied = self._check_staff(request)
        if denied is not None:
            return denied
        box = get_object_or_404(MakerBox, pk=pk)
        png = render_box_label(box)
        response = HttpResponse(png, content_type="image/png")
        response["Content-Disposition"] = f'inline; filename="maker-box-{box.bin_id}.png"'
        return response

    @action(detail=False, methods=["post"], url_path="print-sheet")
    def print_sheet(self, request):
        """Render up to 10 bins into an Avery 5371 (10-up, 2×5) PNG.

        Body: ``{"bin_ids": ["PSB-007", "PSB-008", ...]}``. The caller's
        order is preserved so the warden can lay cards out next to a
        tray of physical bins in a predictable sequence. IDs beyond
        the 10th are dropped (caller paginates); unknown IDs are
        silently skipped so a typo doesn't kill the whole print job.
        The PNG is 8.5"×11" at 300 DPI — scale-to-fit when printing.
        """
        denied = self._check_staff(request)
        if denied is not None:
            return denied

        bin_ids = request.data.get("bin_ids") or []
        if not isinstance(bin_ids, list):
            return Response(
                {"detail": "bin_ids must be a list.", "code": "bad_request"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not bin_ids:
            return Response(
                {"detail": "bin_ids is required.", "code": "bad_request"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        wanted = bin_ids[:CARDS_PER_SHEET]
        boxes_by_id = {b.bin_id: b for b in MakerBox.objects.filter(bin_id__in=wanted)}
        ordered = [boxes_by_id[bid] for bid in wanted if bid in boxes_by_id]
        if not ordered:
            return Response(
                {"detail": "No matching maker boxes found.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        png_bytes = render_avery_5371_sheet(ordered)
        response = HttpResponse(png_bytes, content_type="image/png")
        response["Content-Disposition"] = 'inline; filename="maker-box-sheet.png"'
        return response

    @action(detail=False, methods=["post"], url_path="manual-label")
    def manual_label(self, request):
        denied = self._check_staff(request)
        if denied is not None:
            return denied
        serializer = ManualLabelRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        png = render_box_label(
            None,
            username_override=serializer.validated_data["username"],
            first_name_override=serializer.validated_data.get("first_name", ""),
            last_name_override=serializer.validated_data.get("last_name", ""),
        )
        response = HttpResponse(png, content_type="image/png")
        response["Content-Disposition"] = 'inline; filename="maker-box-manual.png"'
        return response

    # ------------------------------------------------------------------
    # Pre-conversion (PR 2)
    # ------------------------------------------------------------------

    @staticmethod
    def _lookup_payload(lookup, source):
        """Serializer-shaped dict for an identity hit or miss."""
        if lookup is None:
            return {
                "found": False,
                "identity_source": "",
                "username": "",
                "first_name": "",
                "last_name": "",
                "email": "",
                "membership_status": "",
                "expires_at": None,
                "days_remaining": None,
            }
        return {
            "found": True,
            "identity_source": source,
            "username": lookup.username,
            "first_name": lookup.first_name,
            "last_name": lookup.last_name,
            "email": lookup.email,
            "membership_status": lookup.status,
            "expires_at": lookup.expires_at,
            "days_remaining": lookup.days_remaining,
        }

    @action(detail=False, methods=["post"], url_path="lookup")
    def lookup(self, request):
        """Identity-only lookup: run the cascade, return what we found.

        No DB write. The frontend uses this for live preview as a
        staffer types or scans a badge/username — the actual queue
        insert happens in ``pre_convert`` when they hit "Add".
        """
        denied = self._check_staff(request)
        if denied is not None:
            return denied

        serializer = LookupRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        query = serializer.validated_data["query"]

        lookup, source = resolve_identity(query)
        payload = self._lookup_payload(lookup, source)
        return Response(LookupResponseSerializer(payload).data)

    @action(detail=False, methods=["post"], url_path="pre-convert")
    def pre_convert(self, request):
        """Resolve identity and put the user on the pre-conversion queue.

        Idempotent on resolved username: if a pre-conversion row
        already exists, we update its identity fields (in case the
        member's name/email changed) and return it. If the user has
        already been converted (status != pre_conversion AND bin_id is
        set), we return 409 — the queue is for users without a bin
        yet, and reopening a converted row would silently undo PR3's
        work.
        """
        denied = self._check_staff(request)
        if denied is not None:
            return denied

        serializer = PreConvertRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        query = serializer.validated_data["query"]
        notes = serializer.validated_data.get("notes", "")

        lookup, source = resolve_identity(query)
        if lookup is None:
            return Response(
                {
                    "detail": (
                        "No identity matched that badge or username. "
                        "Check the spelling, or have the member scan their badge."
                    ),
                    "code": "not_found",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        existing = MakerBox.objects.filter(assigned_username=lookup.username).first()
        if existing and existing.bin_id and existing.status != MakerBox.STATUS_PRE_CONVERSION:
            return Response(
                {
                    "detail": (
                        f"{lookup.username} is already assigned bin {existing.bin_id}. "
                        "Use the scan screen instead of the pre-conversion queue."
                    ),
                    "code": "already_converted",
                    "bin_id": existing.bin_id,
                },
                status=status.HTTP_409_CONFLICT,
            )

        box = existing or MakerBox(assigned_username=lookup.username, bin_id=None)
        box.assigned_username = lookup.username
        box.first_name = lookup.first_name
        box.last_name = lookup.last_name
        box.email = lookup.email
        box.expires_at = lookup.expires_at
        box.status = MakerBox.STATUS_PRE_CONVERSION
        box.identity_source = source
        box.last_verified_at = timezone.now()
        if notes:
            # Append rather than overwrite so an earlier staffer's note
            # isn't lost when a second staffer re-runs the lookup.
            existing_notes = box.notes or ""
            box.notes = (existing_notes + ("\n" if existing_notes else "") + notes).strip()
        box.save()

        return Response(
            MakerBoxSerializer(box).data,
            status=status.HTTP_200_OK if existing else status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["get"], url_path="pre-conversion-queue")
    def pre_conversion_queue(self, request):
        """List the rows waiting for bin allocation, newest first."""
        denied = self._check_staff(request)
        if denied is not None:
            return denied
        queue = MakerBox.objects.filter(status=MakerBox.STATUS_PRE_CONVERSION).order_by(
            "-created_at"
        )
        return Response(MakerBoxSerializer(queue, many=True).data)

    @action(detail=True, methods=["post"], url_path="email-pickup")
    def email_pickup(self, request, pk=None):
        denied = self._check_staff(request)
        if denied is not None:
            return denied
        box = get_object_or_404(MakerBox, pk=pk)
        recipient = request.data.get("email") or box.email
        if not recipient:
            return Response(
                {"detail": "No email address on file for this bin."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        sent = send_pickup_notification(
            recipient=recipient,
            first_name=box.first_name,
            bin_id=box.bin_id,
        )
        if not sent:
            return Response(
                {"detail": "Email failed to send."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response({"sent": True, "to": recipient})
