"""
Views for membership and SIG management API.
"""

from django.contrib.auth.models import Group
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import SIGAdmin, User
from .serializers import (
    SIGAdminSerializer,
    SIGMemberSerializer,
    SIGSerializer,
    UserSerializer,
)
from .utils import get_user_managed_sigs, is_sig_admin


class SIGViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for SIG (Special Interest Group) operations.

    Allows SIG admins to view their SIGs and get details.
    """

    queryset = Group.objects.all()
    serializer_class = SIGSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Return SIGs that the user administers."""
        user = self.request.user

        # Superusers and staff can see all SIGs
        if user.is_superuser or user.is_staff:
            return Group.objects.all()

        # Regular users see only SIGs they administer
        return get_user_managed_sigs(user)

    def get_serializer_context(self):
        """Add request to serializer context."""
        context = super().get_serializer_context()
        context["request"] = self.request
        return context

    @action(detail=True, methods=["get"])
    def details(self, request, pk=None):
        """Get detailed information about a SIG."""
        sig = self.get_object()

        # Check if user is admin of this SIG
        if not (request.user.is_superuser or request.user.is_staff or is_sig_admin(request.user, sig)):
            return Response(
                {"detail": "You do not have permission to view this SIG."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = self.get_serializer(sig)
        return Response(serializer.data)


class SIGMemberViewSet(viewsets.ViewSet):
    """
    ViewSet for managing SIG members.

    Allows SIG admins to add/remove members from their SIGs.
    """

    permission_classes = [IsAuthenticated]

    def list(self, request, sig_pk=None):
        """List members of a specific SIG."""
        if not sig_pk:
            return Response(
                {"detail": "SIG ID is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            sig = Group.objects.get(pk=sig_pk)
        except Group.DoesNotExist:
            return Response(
                {"detail": "SIG not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Check if user is admin of this SIG
        if not (request.user.is_superuser or request.user.is_staff or is_sig_admin(request.user, sig)):
            return Response(
                {"detail": "You do not have permission to view members of this SIG."},
                status=status.HTTP_403_FORBIDDEN,
            )

        members = sig.user_set.all()
        serializer = SIGMemberSerializer(
            members, many=True, context={"request": request, "group": sig}
        )
        return Response(serializer.data)

    def create(self, request, sig_pk=None):
        """Add a user to a SIG."""
        if not sig_pk:
            return Response(
                {"detail": "SIG ID is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            sig = Group.objects.get(pk=sig_pk)
        except Group.DoesNotExist:
            return Response(
                {"detail": "SIG not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Check if user is admin of this SIG
        if not (request.user.is_superuser or request.user.is_staff or is_sig_admin(request.user, sig)):
            return Response(
                {"detail": "You do not have permission to manage members of this SIG."},
                status=status.HTTP_403_FORBIDDEN,
            )

        user_id = request.data.get("user_id")
        if not user_id:
            return Response(
                {"detail": "user_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return Response(
                {"detail": "User not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Add user to group
        sig.user_set.add(user)

        serializer = UserSerializer(user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def destroy(self, request, sig_pk=None, pk=None):
        """Remove a user from a SIG."""
        if not sig_pk:
            return Response(
                {"detail": "SIG ID is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not pk:
            return Response(
                {"detail": "User ID is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            sig = Group.objects.get(pk=sig_pk)
        except Group.DoesNotExist:
            return Response(
                {"detail": "SIG not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Check if user is admin of this SIG
        if not (request.user.is_superuser or request.user.is_staff or is_sig_admin(request.user, sig)):
            return Response(
                {"detail": "You do not have permission to manage members of this SIG."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response(
                {"detail": "User not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        sig.user_set.remove(user)

        return Response(status=status.HTTP_204_NO_CONTENT)


class SIGAdminViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing SIG admins.

    Only superusers and staff can manage SIG admins.
    """

    queryset = SIGAdmin.objects.all()
    serializer_class = SIGAdminSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Filter by SIG if provided."""
        queryset = super().get_queryset()
        sig_id = self.request.query_params.get("sig_id")
        if sig_id:
            queryset = queryset.filter(group_id=sig_id)
        return queryset.select_related("user", "group")

    def perform_create(self, serializer):
        """Only superusers/staff can create SIG admins."""
        if not (self.request.user.is_superuser or self.request.user.is_staff):
            raise PermissionError("Only superusers and staff can create SIG admins.")
        serializer.save()

    def perform_update(self, serializer):
        """Only superusers/staff can update SIG admins."""
        if not (self.request.user.is_superuser or self.request.user.is_staff):
            raise PermissionError("Only superusers and staff can update SIG admins.")
        serializer.save()

    def perform_destroy(self, instance):
        """Only superusers/staff can delete SIG admins."""
        if not (self.request.user.is_superuser or self.request.user.is_staff):
            raise PermissionError("Only superusers and staff can delete SIG admins.")
        instance.delete()
