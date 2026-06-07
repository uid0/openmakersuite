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
    MakerBoxSerializer,
    ManualLabelRequestSerializer,
    ScanRequestSerializer,
    ScanResponseSerializer,
)
from .services.email_service import send_pickup_notification
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
