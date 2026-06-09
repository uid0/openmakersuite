"""Storage Vision API — slice 2.

Setup CRUD for areas, slots, and cameras + the marker-label PDF/PNG
download. Capture upload, observation review, and the Celery pipeline
land in later slices.

Feature-flag posture (AC-2): when ``STORAGE_VISION_ENABLED`` is False,
every WRITE path returns 503 with code ``feature_disabled``. Reads stay
available so an operator who flipped the flag off can still audit the
existing rows.
"""

from __future__ import annotations

from django.conf import settings
from django.http import HttpResponse

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import VisionArea, VisionCamera, VisionSlot
from .permissions import IsStaffOrLogisticsOrReadOnly
from .serializers import (
    VisionAreaSerializer,
    VisionCameraSerializer,
    VisionCameraTokenSerializer,
    VisionSlotSerializer,
)
from .services.marker_label import render_slot_marker_label


def _feature_disabled_response():
    return Response(
        {
            "detail": "Storage vision is disabled (STORAGE_VISION_ENABLED=False).",
            "code": "feature_disabled",
        },
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


class _FeatureFlagGatedWritesMixin:
    """Block create/update/partial_update/destroy when the feature flag is off."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if not getattr(settings, "STORAGE_VISION_ENABLED", False):
                # Raise via PermissionDenied so DRF's exception handler
                # serializes through the standard error envelope. We
                # override detail / code via the response below.
                self._feature_flag_blocked = True

    def create(self, request, *args, **kwargs):
        if getattr(self, "_feature_flag_blocked", False):
            return _feature_disabled_response()
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        if getattr(self, "_feature_flag_blocked", False):
            return _feature_disabled_response()
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        if getattr(self, "_feature_flag_blocked", False):
            return _feature_disabled_response()
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        if getattr(self, "_feature_flag_blocked", False):
            return _feature_disabled_response()
        return super().destroy(request, *args, **kwargs)


class VisionAreaViewSet(_FeatureFlagGatedWritesMixin, viewsets.ModelViewSet):
    """CRUD on monitored areas (AC-3, AC-4)."""

    queryset = VisionArea.objects.select_related("location").all()
    serializer_class = VisionAreaSerializer
    permission_classes = [IsStaffOrLogisticsOrReadOnly]


class VisionSlotViewSet(_FeatureFlagGatedWritesMixin, viewsets.ModelViewSet):
    """CRUD on marker-backed slots + printable marker download (AC-5, AC-6)."""

    queryset = VisionSlot.objects.select_related("area", "item").all()
    serializer_class = VisionSlotSerializer
    permission_classes = [IsStaffOrLogisticsOrReadOnly]

    def get_queryset(self):
        """Allow filtering by ?area=<id> and ?item=<id> so the Facilities UI
        can scope the list without paginating through everything."""
        qs = super().get_queryset()
        area_id = self.request.query_params.get("area") if hasattr(self, "request") else None
        if area_id:
            qs = qs.filter(area_id=area_id)
        item_id = self.request.query_params.get("item") if hasattr(self, "request") else None
        if item_id:
            qs = qs.filter(item_id=item_id)
        return qs

    @action(
        detail=True,
        methods=["get"],
        url_path="marker",
        permission_classes=[IsAuthenticated],
    )
    def marker(self, request, pk=None):
        """Return a printable PNG label for the slot's marker (AC-6).

        Authenticated read (no write — generating the label can never
        mutate state) so the marker is also usable from a non-staff
        operator view if one shows up later.
        """
        slot = self.get_object()
        png = render_slot_marker_label(slot)
        response = HttpResponse(png, content_type="image/png")
        response["Content-Disposition"] = f'inline; filename="vision-slot-{slot.marker_code}.png"'
        return response


class VisionCameraViewSet(_FeatureFlagGatedWritesMixin, viewsets.ModelViewSet):
    """CRUD on fixed cameras (AC-7).

    Create + ``rotate_token`` are the only paths that emit the raw
    bearer in the response — subsequent reads expose only the
    fingerprint. The serializer chooser ensures the raw token never
    leaks through the standard read path even if a caller passes
    ``include_token=true`` in the query string.
    """

    queryset = VisionCamera.objects.select_related("area").all()
    permission_classes = [IsStaffOrLogisticsOrReadOnly]

    def get_serializer_class(self):
        if self.action in ("create", "rotate_token"):
            return VisionCameraTokenSerializer
        return VisionCameraSerializer

    def perform_create(self, serializer):
        cam = VisionCamera(
            name=serializer.validated_data["name"],
            area=serializer.validated_data.get("area"),
            is_active=serializer.validated_data.get("is_active", True),
        )
        raw = cam.issue_token()
        cam.save()
        # Stash the raw token on the serializer instance so the response
        # serializer renders it ONCE. Subsequent retrievals never see it
        # because the read serializer doesn't include the field.
        cam.raw_token = raw  # type: ignore[attr-defined]
        serializer.instance = cam

    @action(
        detail=True,
        methods=["post"],
        url_path="rotate-token",
        permission_classes=[IsStaffOrLogisticsOrReadOnly],
    )
    def rotate_token(self, request, pk=None):
        """Issue a fresh bearer, invalidate the prior one.

        Returns the same shape as create: the public fields PLUS a
        ``raw_token`` field that exists exactly once per camera-bearer
        lifetime.
        """
        if not getattr(settings, "STORAGE_VISION_ENABLED", False):
            return _feature_disabled_response()
        # Permission shape on the @action decorator already enforces
        # staff/Logistics for unsafe methods; this is just defense in
        # depth in case a future refactor changes the decorator.
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            from .permissions import _is_staff_or_logistics

            if not _is_staff_or_logistics(request.user):
                raise PermissionDenied()

        cam = self.get_object()
        raw = cam.issue_token()
        cam.save(update_fields=["token_hash", "token_fingerprint", "updated_at"])
        cam.raw_token = raw  # type: ignore[attr-defined]

        serializer = VisionCameraTokenSerializer(cam, context={"request": request})
        return Response(serializer.data)
