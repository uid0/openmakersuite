"""
Views for membership and SIG management API.
"""

from django.contrib.auth import authenticate
from django.contrib.auth.models import Group

from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import (
    Committee,
    CommitteeChair,
    SIGAdmin,
    SIGCommittee,
    User,
    UserRegistrationToken,
)
from .serializers import (
    ChangePasswordSerializer,
    CommitteeSerializer,
    CreateSIGSerializer,
    SIGAdminSerializer,
    SIGMemberSerializer,
    SIGSerializer,
    TokenValidationSerializer,
    UserProfileSerializer,
    UserRegistrationSerializer,
    UserSerializer,
)
from .utils import get_user_managed_sigs, is_sig_admin


class CommitteeViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for Committee operations.

    Lists committees with their SIGs. All authenticated users can view committees.
    """

    queryset = Committee.objects.filter(is_active=True)
    serializer_class = CommitteeSerializer
    permission_classes = [IsAuthenticated]

    def get_serializer_context(self):
        """Add request to serializer context."""
        context = super().get_serializer_context()
        context["request"] = self.request
        return context


class SIGViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for SIG (Special Interest Group) operations.

    Allows SIG admins to view their SIGs and get details.
    Committee Chairs can create new SIGs for their committees.
    """

    queryset = Group.objects.all()
    serializer_class = SIGSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Return SIGs that the user administers or can see."""
        user = self.request.user

        # Superusers and staff can see all SIGs
        if user.is_superuser or user.is_staff:
            return Group.objects.all()

        # Committee Chairs can see SIGs in their committees
        user_committees = CommitteeChair.get_user_committees(user)
        if user_committees.exists():
            sigs_from_committees = Group.objects.filter(
                committee_membership__committee__in=user_committees
            )
            # Also include SIGs they administer
            user_managed = get_user_managed_sigs(user)
            return (sigs_from_committees | user_managed).distinct()

        # Regular users see only SIGs they administer
        return get_user_managed_sigs(user)

    def get_serializer_context(self):
        """Add request to serializer context."""
        context = super().get_serializer_context()
        context["request"] = self.request
        return context

    @action(detail=False, methods=["post"])
    def create_sig(self, request):
        """
        Create a new SIG for a committee.

        Only Committee Chairs can create SIGs for their committees.
        """
        serializer = CreateSIGSerializer(data=request.data, context={"request": request})
        if serializer.is_valid():
            name = serializer.validated_data["name"]
            committee_id = serializer.validated_data["committee_id"]

            try:
                committee = Committee.objects.get(pk=committee_id, is_active=True)
            except Committee.DoesNotExist:
                return Response(
                    {"detail": "Committee not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Create the Group (SIG)
            sig = Group.objects.create(name=name)

            # Link SIG to committee
            SIGCommittee.objects.create(group=sig, committee=committee)

            # Make the creator a SIG admin
            SIGAdmin.objects.create(user=request.user, group=sig, is_active=True)

            # Return the created SIG
            sig_serializer = self.get_serializer(sig)
            return Response(sig_serializer.data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

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

        # Check if user is admin of this SIG or chair of the committee
        user_can_view = False
        if request.user.is_superuser or request.user.is_staff:
            user_can_view = True
        elif is_sig_admin(request.user, sig):
            user_can_view = True
        else:
            # Check if user is a chair of the committee this SIG belongs to
            try:
                sig_committee = SIGCommittee.objects.get(group=sig)
                if CommitteeChair.is_committee_chair(request.user, sig_committee.committee):
                    user_can_view = True
            except SIGCommittee.DoesNotExist:
                pass

        if not user_can_view:
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
