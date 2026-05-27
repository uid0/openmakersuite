"""
Views for notification API.
"""

import hashlib

from django.db.models import Count, Max, Q

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import Notification, NotificationPreference
from .serializers import (
    NotificationCreateSerializer,
    NotificationPreferenceSerializer,
    NotificationSerializer,
)


class NotificationViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing user notifications.

    Users can only see and manage their own notifications.
    """

    authentication_classes = (JWTAuthentication,)
    permission_classes = [IsAuthenticated]
    serializer_class = NotificationSerializer

    def get_queryset(self):
        """Return only notifications for the current user."""
        queryset = Notification.objects.filter(user=self.request.user)

        # Filter by read status if provided
        read_param = self.request.query_params.get("read", None)
        if read_param is not None:
            read_value = read_param.lower() == "true"
            queryset = queryset.filter(read=read_value)

        return queryset

    def _compute_etag(self) -> str:
        """Cheap aggregate-only fingerprint of the user's notification state.

        Polls hammer this list endpoint every few seconds; serializing the
        full result set on every request is wasted work when nothing has
        changed. The fingerprint covers:

          - latest `created_at` → flips when a new notification arrives
          - total count          → flips on create + destroy
          - unread count         → flips when the user (or any code path)
                                    toggles `read=True/False`

        Query-string params are folded in so `/?read=true` and
        `/?read=false` get distinct cache entries. One SQL round-trip; no
        row materialization. Weak ETag (`W/...`) because two backend
        replicas can serialize identical state with byte-different JSON
        (key ordering, whitespace).
        """
        agg = Notification.objects.filter(user=self.request.user).aggregate(
            latest=Max("created_at"),
            total=Count("id"),
            unread=Count("id", filter=Q(read=False)),
        )
        filter_key = ",".join(f"{k}={v}" for k, v in sorted(self.request.query_params.items()))
        raw = (
            f"u{self.request.user.pk}|"
            f"latest={agg['latest']}|total={agg['total']}|"
            f"unread={agg['unread']}|filter={filter_key}"
        ).encode()
        return 'W/"' + hashlib.sha256(raw).hexdigest()[:16] + '"'

    def list(self, request, *args, **kwargs):
        """Conditional GET on the notification list.

        Returns 304 Not Modified when the client's `If-None-Match`
        matches the current aggregate fingerprint. Frontends keep their
        existing poll cadence; most polls become 304s with no body.
        """
        queryset = self.filter_queryset(self.get_queryset())
        etag = self._compute_etag()

        if_none_match = request.headers.get("If-None-Match", "")
        if if_none_match and etag in if_none_match:
            response = Response(status=status.HTTP_304_NOT_MODIFIED)
            response["ETag"] = etag
            response["Cache-Control"] = "private, must-revalidate"
            return response

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            response = self.get_paginated_response(serializer.data)
        else:
            serializer = self.get_serializer(queryset, many=True)
            response = Response(serializer.data)
        response["ETag"] = etag
        response["Cache-Control"] = "private, must-revalidate"
        return response

    def get_serializer_class(self):
        """Use create serializer for POST requests."""
        if self.action == "create":
            return NotificationCreateSerializer
        return NotificationSerializer

    def perform_create(self, serializer):
        """Set the user to the current user when creating."""
        serializer.save(user=self.request.user)

    @action(detail=True, methods=["post"], url_path="mark-read", url_name="mark-read")
    def mark_read(self, request, pk=None):
        """Mark a notification as read."""
        notification = self.get_object()
        notification.mark_as_read()
        return Response({"status": "marked as read"})

    @action(detail=False, methods=["post"], url_path="mark-all-read", url_name="mark-all-read")
    def mark_all_read(self, request):
        """Mark all notifications for the current user as read."""
        updated = Notification.objects.filter(user=request.user, read=False).update(read=True)
        return Response({"status": "marked all as read", "updated": updated})


class NotificationPreferenceView(APIView):
    """
    Singleton view for the current user's notification preferences.

    GET/PUT/PATCH all act on the row owned by request.user, auto-creating it
    with defaults on first access.
    """

    authentication_classes = (JWTAuthentication,)
    permission_classes = [IsAuthenticated]

    def _get_preferences(self, user):
        preferences, _ = NotificationPreference.objects.get_or_create(user=user)
        return preferences

    def get(self, request):
        preferences = self._get_preferences(request.user)
        serializer = NotificationPreferenceSerializer(preferences)
        return Response(serializer.data)

    def put(self, request):
        preferences = self._get_preferences(request.user)
        serializer = NotificationPreferenceSerializer(preferences, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def patch(self, request):
        return self.put(request)
