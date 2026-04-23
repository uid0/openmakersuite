"""
Views for notification API.
"""

from rest_framework import viewsets
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
