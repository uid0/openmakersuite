"""
Views for membership and SIG management API.
"""

from django.contrib.auth import authenticate
from django.contrib.auth.models import Group

from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import SIGAdmin, User, UserRegistrationToken
from .serializers import (
    ChangePasswordSerializer,
    SIGAdminSerializer,
    SIGCreateSerializer,
    SIGMemberSerializer,
    SIGSerializer,
    TokenValidationSerializer,
    UserProfileSerializer,
    UserRegistrationSerializer,
    UserSerializer,
)
from .utils import get_user_managed_sigs, is_sig_admin


class SIGViewSet(viewsets.ModelViewSet):
    """
    ViewSet for SIG (Special Interest Group) operations.

    Read access is open to SIG admins (for their SIGs) and staff (all SIGs).
    Create/update/delete is restricted to staff and superusers.
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

    def get_serializer_class(self):
        """Use a writable serializer for create/update actions."""
        if self.action in ("create", "update", "partial_update"):
            return SIGCreateSerializer
        return SIGSerializer

    def get_serializer_context(self):
        """Add request to serializer context."""
        context = super().get_serializer_context()
        context["request"] = self.request
        return context

    def _require_staff(self):
        user = self.request.user
        if not (user.is_authenticated and (user.is_superuser or user.is_staff)):
            raise PermissionDenied("Only staff users can manage SIGs.")

    def perform_create(self, serializer):
        """Only staff/superusers can create SIGs."""
        self._require_staff()
        serializer.save()

    def perform_update(self, serializer):
        """Only staff/superusers can update SIGs."""
        self._require_staff()
        serializer.save()

    def perform_destroy(self, instance):
        """Only staff/superusers can delete SIGs."""
        self._require_staff()
        instance.delete()

    @action(detail=True, methods=["get"])
    def details(self, request, pk=None):
        """Get detailed information about a SIG."""
        from django.contrib.auth.models import Group

        try:
            sig = Group.objects.get(pk=pk)
        except Group.DoesNotExist:
            return Response(
                {"detail": "SIG not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Check if user is admin of this SIG
        if not (
            request.user.is_superuser or request.user.is_staff or is_sig_admin(request.user, sig)
        ):
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
        if not (
            request.user.is_superuser or request.user.is_staff or is_sig_admin(request.user, sig)
        ):
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
        if not (
            request.user.is_superuser or request.user.is_staff or is_sig_admin(request.user, sig)
        ):
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
        if not (
            request.user.is_superuser or request.user.is_staff or is_sig_admin(request.user, sig)
        ):
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


class UserProfileViewSet(viewsets.ViewSet):
    """
    ViewSet for user profile management.

    Users can view and update their own profile.
    """

    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["get"])
    def me(self, request):
        """Get current user's profile."""
        serializer = UserProfileSerializer(request.user, context={"request": request})
        return Response(serializer.data)

    @action(detail=False, methods=["put", "patch"])
    def update_me(self, request):
        """Update current user's profile."""
        serializer = UserProfileSerializer(
            request.user, data=request.data, context={"request": request}, partial=True
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def change_password(request):
    """
    Change user password.

    Requires old password verification and new password confirmation.
    """
    serializer = ChangePasswordSerializer(data=request.data)
    if serializer.is_valid():
        user = request.user
        old_password = serializer.validated_data["old_password"]
        new_password = serializer.validated_data["new_password"]

        # Verify old password
        if not authenticate(username=user.username, password=old_password):
            return Response(
                {"old_password": ["Invalid password."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Set new password
        user.set_password(new_password)
        user.save()

        return Response({"message": "Password changed successfully."}, status=status.HTTP_200_OK)

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(["POST"])
@permission_classes([])  # Public endpoint
def validate_registration_token(request):
    """
    Validate a registration token.

    This endpoint allows checking if a token is valid before attempting registration.
    """
    serializer = TokenValidationSerializer(data=request.data)
    if serializer.is_valid():
        token = serializer.validated_data["token"]
        registration_token = UserRegistrationToken.objects.get(token=token)
        user_serializer = UserSerializer(registration_token.user)
        return Response(
            {
                "valid": True,
                "user": user_serializer.data,
                "expires_at": registration_token.expires_at.isoformat(),
            },
            status=status.HTTP_200_OK,
        )
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(["POST"])
@permission_classes([])  # Public endpoint
def register_user_with_token(request):
    """
    Complete user registration using a registration token.

    This endpoint allows users to set their password using a one-time registration token.
    """
    serializer = UserRegistrationSerializer(data=request.data)
    if serializer.is_valid():
        user = serializer.save()
        user_serializer = UserSerializer(user)
        return Response(
            {
                "message": "Registration completed successfully.",
                "user": user_serializer.data,
            },
            status=status.HTTP_200_OK,
        )
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
