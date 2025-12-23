"""
Views for notification API.
"""

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
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


class NotificationPreferenceViewSet(viewsets.ViewSet):
    """
    ViewSet for managing user notification preferences.

    Auto-creates preferences on first access with default values.
    """

    authentication_classes = (JWTAuthentication,)
    permission_classes = [IsAuthenticated]
    serializer_class = NotificationPreferenceSerializer

    def list(self, request):
        """Get current user's notification preferences."""
        preferences, created = NotificationPreference.objects.get_or_create(user=request.user)
        serializer = self.get_serializer(preferences)
        return Response(serializer.data)

    def retrieve(self, request, pk=None):
        """Get current user's notification preferences (alias for list)."""
        preferences, created = NotificationPreference.objects.get_or_create(user=request.user)
        serializer = self.get_serializer(preferences)
        return Response(serializer.data)

    def update(self, request):
        """Update current user's notification preferences."""
        preferences, created = NotificationPreference.objects.get_or_create(user=request.user)
        serializer = self.get_serializer(preferences, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
