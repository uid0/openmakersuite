"""DRF serializers for the storage_vision setup surfaces.

VisionArea + VisionSlot are plain ModelSerializers. VisionCamera has
two shapes:

  - :class:`VisionCameraSerializer` — the public read shape: no token,
    only the 16-hex fingerprint and last-seen metadata.
  - :class:`VisionCameraTokenSerializer` — the response shape for the
    one create / rotate-token call that returns the RAW bearer to the
    operator. The viewset selects this serializer for those two paths
    only; subsequent retrieves go through the public read shape.
"""

from __future__ import annotations

from io import BytesIO

from django.conf import settings

from PIL import Image, UnidentifiedImageError
from rest_framework import serializers

from .models import VisionArea, VisionCamera, VisionCapture, VisionSlot

ALLOWED_UPLOAD_CONTENT_TYPES = ("image/jpeg", "image/png")


class VisionAreaSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source="location.name", read_only=True)

    class Meta:
        model = VisionArea
        fields = [
            "id",
            "name",
            "location",
            "location_name",
            "description",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class VisionSlotSerializer(serializers.ModelSerializer):
    area_name = serializers.CharField(source="area.name", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)

    class Meta:
        model = VisionSlot
        fields = [
            "id",
            "area",
            "area_name",
            "item",
            "item_name",
            "marker_code",
            "empty_low_confidence_threshold",
            "notes",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class VisionCameraSerializer(serializers.ModelSerializer):
    """Public read shape. NEVER returns the raw bearer token."""

    area_name = serializers.CharField(source="area.name", read_only=True, allow_null=True)

    class Meta:
        model = VisionCamera
        fields = [
            "id",
            "name",
            "area",
            "area_name",
            "token_fingerprint",
            "last_seen_at",
            "last_seen_status",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "token_fingerprint",
            "last_seen_at",
            "last_seen_status",
            "created_at",
            "updated_at",
        ]


class VisionCameraTokenSerializer(serializers.ModelSerializer):
    """Create / rotate response shape — includes the raw bearer ONCE."""

    raw_token = serializers.CharField(read_only=True)
    area_name = serializers.CharField(source="area.name", read_only=True, allow_null=True)

    class Meta:
        model = VisionCamera
        fields = [
            "id",
            "name",
            "area",
            "area_name",
            "token_fingerprint",
            "raw_token",
            "last_seen_at",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "token_fingerprint",
            "raw_token",
            "last_seen_at",
            "created_at",
            "updated_at",
        ]


class VisionCaptureSerializer(serializers.ModelSerializer):
    """Read shape for an uploaded capture.

    Slice-3 surface: includes the bookkeeping fields the frontend
    polls for. The original_image URL is included so the review UI
    (slice 5) can render the thumbnail without a second round trip;
    we don't gate it behind extra auth because the staff/Logistics
    reads gate the list endpoint upstream.
    """

    area_name = serializers.CharField(source="area.name", read_only=True)

    class Meta:
        model = VisionCapture
        fields = [
            "id",
            "area",
            "area_name",
            "source",
            "camera",
            "uploaded_by",
            "original_image",
            "captured_at",
            "received_at",
            "status",
            "processor_version",
            "markers_detected",
            "failure_reason",
            "failure_code",
            "queued_at",
            "processing_at",
            "processed_at",
            "failed_at",
        ]
        read_only_fields = fields  # captures are write-once at create


class VisionCaptureCreateSerializer(serializers.ModelSerializer):
    """Write-side serializer for capture uploads (AC-9, AC-10, AC-12).

    Source / camera / uploaded_by are stamped by the view from the
    authenticated principal — never accepted from the request body.

    ``area`` is declared optional here so a camera bound to an area
    can omit it; the view falls back to camera.area in that case.
    For phone uploads the view re-validates that area is present.
    """

    area = serializers.PrimaryKeyRelatedField(
        queryset=VisionArea.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = VisionCapture
        fields = ["area", "original_image", "captured_at"]

    def validate_original_image(self, value):
        max_bytes = getattr(settings, "STORAGE_VISION_MAX_UPLOAD_BYTES", 10 * 1024 * 1024)
        size = getattr(value, "size", None)
        if size is not None and size > max_bytes:
            raise serializers.ValidationError(
                f"Image exceeds the {max_bytes}-byte cap.",
            )

        # AC-12: file-system paths and raw exception traces must not
        # leak. We do our own content-type + Pillow decode check and
        # surface a stable, sanitized message.
        content_type = getattr(value, "content_type", None)
        if content_type and content_type not in ALLOWED_UPLOAD_CONTENT_TYPES:
            raise serializers.ValidationError(
                "Only JPEG or PNG uploads are accepted.",
            )

        try:
            data = value.read()
            value.seek(0)
            Image.open(BytesIO(data)).verify()
        except UnidentifiedImageError:
            raise serializers.ValidationError("Image could not be decoded.")
        except Exception:  # noqa: BLE001 — sanitize everything else
            raise serializers.ValidationError("Image could not be decoded.")
        return value
