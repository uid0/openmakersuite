"""
Views for the screens app.

Public endpoints (no auth):
  - GET  /api/screens/kiosk/<slug>/?token=...      kiosk display payload
  - POST /api/screens/kiosk/<slug>/heartbeat/?token=...  kiosk heartbeat

Authenticated endpoints:
  - /api/screens/screens/   (CRUD + /status/)
  - /api/screens/messages/  (system message CRUD — staff only)
  - /api/screens/blocks/    (content block CRUD — screen owners)
"""

import hmac

from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from membership.utils import get_user_managed_sigs, is_logistics_member

from .models import Screen, ScreenContentBlock, ScreenHeartbeat, SystemMessage
from .permissions import user_can_manage_screen, user_can_manage_system_messages
from .serializers import (
    ScreenContentBlockSerializer,
    ScreenHeartbeatSerializer,
    ScreenSerializer,
    ScreenStatusSerializer,
    SystemMessageSerializer,
)


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or None


def _token_matches(screen_token: str, provided: str) -> bool:
    return hmac.compare_digest(screen_token or "", provided or "")


def _build_kiosk_payload(screen):
    """Assemble the full payload a kiosk consumes for rendering."""
    system_messages = [
        {
            "id": str(m.id),
            "title": m.title,
            "body": m.body,
            "level": m.level,
        }
        for m in SystemMessage.active_for_now().order_by("-level", "-created_at")
    ]
    content_blocks = [
        {
            "id": str(b.id),
            "block_type": b.block_type,
            "title": b.title,
            "body": b.body,
            "config": b.config,
            "order": b.order,
        }
        for b in screen.content_blocks.filter(is_enabled=True).order_by("order", "created_at")
    ]
    return {
        "screen": {
            "id": str(screen.id),
            "slug": screen.slug,
            "name": screen.name,
            "description": screen.description,
            "rotation_interval_seconds": screen.rotation_interval_seconds,
            "refresh_interval_seconds": screen.refresh_interval_seconds,
        },
        "system_messages": system_messages,
        "content_blocks": content_blocks,
        "generated_at": timezone.now(),
    }


@api_view(["GET"])
@permission_classes([AllowAny])
def kiosk_payload(request, slug):
    """
    Return the payload for rendering a kiosk display.

    Requires an `access_token` (either as ?token=... or in the X-Screen-Token
    header) matching the screen. Inactive screens are hidden.
    """
    token = request.query_params.get("token") or request.META.get("HTTP_X_SCREEN_TOKEN")
    if not token:
        return Response({"detail": "Missing access token."}, status=status.HTTP_401_UNAUTHORIZED)

    screen = get_object_or_404(Screen, slug=slug, is_active=True)
    if not _token_matches(screen.access_token, token):
        return Response({"detail": "Invalid access token."}, status=status.HTTP_403_FORBIDDEN)

    return Response(_build_kiosk_payload(screen))


@api_view(["POST"])
@permission_classes([AllowAny])
def kiosk_heartbeat(request, slug):
    """Record a heartbeat from a kiosk endpoint."""
    token = request.query_params.get("token") or request.META.get("HTTP_X_SCREEN_TOKEN")
    if not token:
        return Response({"detail": "Missing access token."}, status=status.HTTP_401_UNAUTHORIZED)

    screen = get_object_or_404(Screen, slug=slug, is_active=True)
    if not _token_matches(screen.access_token, token):
        return Response({"detail": "Invalid access token."}, status=status.HTTP_403_FORBIDDEN)

    heartbeat = ScreenHeartbeat.objects.create(
        screen=screen,
        user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:255],
        client_ip=_client_ip(request),
        content_version=(
            request.data.get("content_version", "")[:64] if isinstance(request.data, dict) else ""
        ),
    )
    return Response(ScreenHeartbeatSerializer(heartbeat).data, status=status.HTTP_201_CREATED)


class ScreenViewSet(viewsets.ModelViewSet):
    """
    CRUD for screens.

    Staff / logistics members see all screens. SIG admins see screens owned
    by SIGs they manage. Regular members cannot see or modify screens.
    """

    queryset = Screen.objects.select_related("sig", "location").prefetch_related("content_blocks")
    serializer_class = ScreenSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "slug"

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if user.is_superuser or user.is_staff or is_logistics_member(user):
            return queryset
        managed_sigs = get_user_managed_sigs(user)
        if managed_sigs.exists():
            return queryset.filter(sig__in=managed_sigs)
        return queryset.none()

    def perform_create(self, serializer):
        user = self.request.user
        sig = serializer.validated_data.get("sig")
        if not (user.is_superuser or user.is_staff or is_logistics_member(user)):
            if sig is None or not get_user_managed_sigs(user).filter(pk=sig.pk).exists():
                raise PermissionDenied("You can only create screens for SIGs you administer.")
        serializer.save(created_by=user)

    def perform_update(self, serializer):
        if not user_can_manage_screen(self.request.user, serializer.instance):
            raise PermissionDenied("You cannot modify this screen.")
        serializer.save()

    def perform_destroy(self, instance):
        if not user_can_manage_screen(self.request.user, instance):
            raise PermissionDenied("You cannot delete this screen.")
        instance.delete()

    @action(detail=False, methods=["get"], url_path="status")
    def status_overview(self, request):
        """Return a lightweight status view of all visible screens."""
        qs = self.get_queryset()
        data = ScreenStatusSerializer(qs, many=True).data
        return Response({"count": len(data), "screens": data})

    @action(detail=True, methods=["post"], url_path="rotate-token")
    def rotate_token(self, request, slug=None):
        """Rotate the access token for this screen."""
        screen = self.get_object()
        if not user_can_manage_screen(request.user, screen):
            raise PermissionDenied("You cannot rotate tokens for this screen.")
        from .models import generate_screen_token

        screen.access_token = generate_screen_token()
        screen.save(update_fields=["access_token", "updated_at"])
        return Response({"access_token": screen.access_token})


class ScreenContentBlockViewSet(viewsets.ModelViewSet):
    """CRUD for content blocks. Scoped to screens the user can manage."""

    queryset = ScreenContentBlock.objects.select_related("screen", "screen__sig")
    serializer_class = ScreenContentBlockSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if user.is_superuser or user.is_staff or is_logistics_member(user):
            return queryset
        managed_sigs = get_user_managed_sigs(user)
        if managed_sigs.exists():
            return queryset.filter(screen__sig__in=managed_sigs)
        return queryset.none()

    def _check_can_manage(self, screen):
        if not user_can_manage_screen(self.request.user, screen):
            raise PermissionDenied("You cannot manage content for this screen.")

    def perform_create(self, serializer):
        self._check_can_manage(serializer.validated_data["screen"])
        serializer.save()

    def perform_update(self, serializer):
        self._check_can_manage(serializer.instance.screen)
        serializer.save()

    def perform_destroy(self, instance):
        self._check_can_manage(instance.screen)
        instance.delete()


class SystemMessageViewSet(viewsets.ModelViewSet):
    """CRUD for system-wide messages. Staff / logistics only for writes."""

    queryset = SystemMessage.objects.all()
    serializer_class = SystemMessageSerializer
    permission_classes = [IsAuthenticated]

    def _check_staff(self):
        if not user_can_manage_system_messages(self.request.user):
            raise PermissionDenied("Only staff may manage system messages.")

    def perform_create(self, serializer):
        self._check_staff()
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        self._check_staff()
        serializer.save()

    def perform_destroy(self, instance):
        self._check_staff()
        instance.delete()
