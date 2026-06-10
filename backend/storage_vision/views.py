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
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone

from rest_framework import status, viewsets
from rest_framework.authentication import BasicAuthentication, SessionAuthentication
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from .authentication import VisionCameraTokenAuthentication
from .models import VisionArea, VisionCamera, VisionCapture, VisionSlot
from .permissions import (
    IsCameraOrStaffOrLogistics,
    IsStaffOrLogisticsOrReadOnly,
    _is_staff_or_logistics,
)
from .serializers import (
    VisionAreaSerializer,
    VisionCameraSerializer,
    VisionCameraTokenSerializer,
    VisionCaptureCreateSerializer,
    VisionCaptureSerializer,
    VisionSlotSerializer,
)
from .services.marker_label import render_slot_marker_label
from .tasks import process_capture


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

    @action(
        detail=True,
        methods=["post"],
        url_path="heartbeat",
        authentication_classes=[VisionCameraTokenAuthentication],
        permission_classes=[],
    )
    def heartbeat(self, request, pk=None):
        """Camera-side liveness ping (AC-8).

        Auth is the scoped camera bearer ONLY — no JWT path, because a
        human operator pressing "ping" doesn't tell us anything about
        whether the device is alive. The camera resolved by the bearer
        must match the URL ``pk`` (otherwise a camera with one bearer
        could ping another camera's row).

        Body is optional. Accepted shape:
          ``{"status": "ok" | "degraded" | "error", "note": "..."}``
        Stamps ``last_seen_at = now()`` regardless of body, plus
        ``last_seen_status`` if a value was supplied.

        The feature flag is intentionally NOT enforced — turning the
        feature off shouldn't make the device think the network is
        broken. Reads stay on too.
        """
        camera = request.auth
        if not isinstance(camera, VisionCamera):
            # Bearer was missing entirely. The authentication class
            # already 401s on an invalid bearer; this path catches the
            # "no header at all" case where authenticate() returned None.
            return Response(
                {"detail": "Authentication credentials were not provided."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        target = self.get_object()
        if camera.pk != target.pk:
            # Cross-camera ping attempt — refuse silently with 404 so
            # we don't tell a stolen bearer that the other ID exists.
            return Response(status=status.HTTP_404_NOT_FOUND)

        body = request.data if isinstance(request.data, dict) else {}
        update_fields = ["last_seen_at"]
        camera.last_seen_at = timezone.now()
        # last_seen_status is a free-form JSON blob — accept any dict
        # body the device sends (firmware version, queue depth, last
        # error string). Strings get wrapped so the column stays a dict.
        if body:
            if isinstance(body, dict):
                camera.last_seen_status = body
            else:
                camera.last_seen_status = {"status": str(body)}
            update_fields.append("last_seen_status")
        camera.save(update_fields=update_fields)

        serializer = VisionCameraSerializer(camera, context={"request": request})
        return Response(serializer.data)


class VisionCaptureViewSet(_FeatureFlagGatedWritesMixin, viewsets.ModelViewSet):
    """Capture upload + listing (AC-9, AC-10, AC-11, AC-12).

    Dual auth: a request carrying ``X-Vision-Camera-Token`` is treated
    as a camera POSTing on its own behalf (source=camera); a request
    carrying a normal JWT for a staff/Logistics user is treated as a
    phone upload (source=phone). Anonymous callers are rejected.

    Write surface is POST-only — there's no point in PATCH/PUT/DELETE
    for an immutable capture row. The mixin still gates the path on
    the feature flag for symmetry with the other viewsets.
    """

    queryset = VisionCapture.objects.select_related("area", "camera", "uploaded_by").all()
    http_method_names = ["get", "head", "options", "post"]
    authentication_classes = [
        VisionCameraTokenAuthentication,
        JWTAuthentication,
        SessionAuthentication,
        BasicAuthentication,
    ]
    permission_classes = [IsCameraOrStaffOrLogistics]
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        qs = super().get_queryset()
        if hasattr(self, "request"):
            area_id = self.request.query_params.get("area")
            if area_id:
                qs = qs.filter(area_id=area_id)
            status_value = self.request.query_params.get("status")
            if status_value:
                qs = qs.filter(status=status_value)
        return qs

    def get_serializer_class(self):
        if self.action == "create":
            return VisionCaptureCreateSerializer
        return VisionCaptureSerializer

    def list(self, request, *args, **kwargs):
        # Reads require a logged-in operator — a camera bearer can
        # write its own captures but shouldn't be able to enumerate
        # the queue.
        if not _is_staff_or_logistics(request.user):
            raise PermissionDenied()
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        if not _is_staff_or_logistics(request.user):
            raise PermissionDenied()
        return super().retrieve(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        if getattr(self, "_feature_flag_blocked", False):
            return _feature_disabled_response()

        write_serializer = self.get_serializer(data=request.data)
        write_serializer.is_valid(raise_exception=True)

        # Stamp source / camera / uploaded_by from the authenticated
        # principal — the request body must NOT be able to influence
        # these (a camera bearer claiming source=phone would dodge
        # camera-side audit logs).
        camera = request.auth if isinstance(request.auth, VisionCamera) else None
        if camera is not None:
            source = VisionCapture.SOURCE_CAMERA
            uploaded_by = None
            area = write_serializer.validated_data.get("area") or camera.area
        else:
            source = VisionCapture.SOURCE_PHONE
            uploaded_by = request.user
            area = write_serializer.validated_data.get("area")
            if area is None:
                # Phones must spell out the area — there's no implicit
                # fallback like there is for cameras.
                return Response(
                    {"area": ["This field is required for phone uploads."]},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        with transaction.atomic():
            capture = VisionCapture.objects.create(
                area=area,
                source=source,
                camera=camera,
                uploaded_by=uploaded_by,
                original_image=write_serializer.validated_data["original_image"],
                captured_at=write_serializer.validated_data.get("captured_at"),
                received_at=timezone.now(),
                status=VisionCapture.STATUS_QUEUED,
                queued_at=timezone.now(),
            )

        # Enqueued AFTER the inner atomic commits — by the time we
        # call .delay() the row is durable. We can't use on_commit
        # because the outer request transaction (test client and ATOMIC
        # _REQUESTS in prod) wouldn't fire the callback until after the
        # response is already on its way out, and we'd rather see the
        # broker error inline than discover it next morning.
        process_capture.delay(capture.pk)

        read_serializer = VisionCaptureSerializer(capture, context={"request": request})
        return Response(read_serializer.data, status=status.HTTP_202_ACCEPTED)
