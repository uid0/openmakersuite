"""Camera-token authentication for storage-vision uploads (AC-10).

A fixed camera authenticates via the ``X-Vision-Camera-Token`` header.
The header value is the raw bearer that ``VisionCamera.issue_token``
emitted exactly once when the camera was provisioned.

DRF authentication contract:

  - No header, or empty header → return None so the regular auth chain
    (JWT, session) gets a chance. A staff user hitting this endpoint
    from their phone with a JWT still authenticates normally.
  - Header present but the token doesn't match an active camera →
    raise AuthenticationFailed (401). We're explicit about the failure
    rather than silently falling through, because a phone wouldn't
    send this header by mistake.
  - Header valid → return (AnonymousUser, VisionCamera). request.user
    stays anonymous on purpose — cameras are not Users — but
    request.auth carries the camera instance so the view layer can
    attribute the capture.
"""

from __future__ import annotations

from django.contrib.auth.models import AnonymousUser

from rest_framework import authentication, exceptions

from .models import VisionCamera

VISION_CAMERA_TOKEN_HEADER = "HTTP_X_VISION_CAMERA_TOKEN"


class VisionCameraTokenAuthentication(authentication.BaseAuthentication):
    """Resolves X-Vision-Camera-Token → VisionCamera (or fails 401)."""

    def authenticate(self, request):
        raw = request.META.get(VISION_CAMERA_TOKEN_HEADER, "").strip()
        if not raw:
            return None
        camera = VisionCamera.find_by_token(raw)
        if camera is None:
            raise exceptions.AuthenticationFailed("Invalid camera token.")
        return (AnonymousUser(), camera)

    def authenticate_header(self, request):
        # Returned in the WWW-Authenticate header on 401. Custom scheme
        # so a phone reading this header knows it's a camera-only path.
        return "X-Vision-Camera-Token"
