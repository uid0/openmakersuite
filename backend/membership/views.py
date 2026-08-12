"""
Views for membership and SIG management API.
"""

from django.contrib.auth import authenticate
from django.contrib.auth.models import Group
from django.db.models import Q

from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from .models import SIGAdmin, User, UserRegistrationToken
from .serializers import (
    ChangePasswordSerializer,
    SIGAdminSerializer,
    SIGCreateSerializer,
    SIGMemberSerializer,
    SIGSerializer,
    TokenValidationSerializer,
    UserDirectorySerializer,
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


class UserDirectoryViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only, staff-only directory of users for access-control pickers (op-tup).

    The access-control frontend (asset authorization grant, badge enrollment)
    needs to resolve a member to a user id and show their current badge, but no
    general user listing existed. This provides a minimal one:

    - ``?search=`` matches username / first / last name / email (case-insensitive);
    - ``?has_badge=true`` (or ``false``) narrows to members with (or without) a
      badge, for the badge-enrollment admin.

    Results are ordered by username; the global PageNumberPagination bounds the
    response so a large roster never returns unbounded.
    """

    serializer_class = UserDirectorySerializer
    permission_classes = [IsAdminUser]

    def get_queryset(self):
        qs = User.objects.all().order_by("username")
        params = self.request.query_params
        search = (params.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(username__icontains=search)
                | Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
                | Q(email__icontains=search)
            )
        has_badge = params.get("has_badge")
        if has_badge is not None:
            if has_badge.lower() in ("1", "true", "yes"):
                qs = qs.exclude(badge_number__isnull=True).exclude(badge_number="")
            elif has_badge.lower() in ("0", "false", "no"):
                qs = qs.filter(Q(badge_number__isnull=True) | Q(badge_number=""))
        return qs


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


# ---------------------------------------------------------------------
# Invite codes — staff-mint, anonymous-redeem self-signup workflow.
# ---------------------------------------------------------------------

from django.utils import timezone  # noqa: E402

from rest_framework.permissions import AllowAny, IsAdminUser  # noqa: E402

from .models import InviteCode  # noqa: E402
from .serializers import (  # noqa: E402
    InviteCodeCreateSerializer,
    InviteCodeListSerializer,
    InviteCodeRedeemSerializer,
)


def _client_ip(request) -> str | None:
    """Best-effort client IP, honoring X-Forwarded-For when behind nginx."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


class InviteCodeViewSet(viewsets.ModelViewSet):
    """Staff-only CRUD for invite codes.

    Reads + writes are admin-gated; the public redemption path is the
    separate `redeem_invite_code` view below (AllowAny).
    """

    permission_classes = [IsAdminUser]
    queryset = InviteCode.objects.select_related("created_by", "redeemed_by").prefetch_related(
        "intended_groups"
    )

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return InviteCodeCreateSerializer
        return InviteCodeListSerializer

    def perform_create(self, serializer):
        # Mint the random code server-side; never trust client-supplied codes.
        serializer.save(
            code=InviteCode.generate_code(),
            created_by=self.request.user,
        )

    @action(detail=True, methods=["post"], permission_classes=[IsAdminUser])
    def revoke(self, request, pk=None):
        """Flip is_active to False so the code can no longer be redeemed."""
        invite = self.get_object()
        if invite.redeemed:
            return Response(
                {"detail": "Already redeemed; revocation is a no-op."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        invite.is_active = False
        invite.save(update_fields=["is_active"])
        serializer = InviteCodeListSerializer(invite)
        return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([AllowAny])
def invite_code_preview(request):
    """Anonymous preview: shows the invitee what they're signing up for.

    `?code=<code>` returns the intended_label + group names so the
    public redeem page can show 'Sign up as Board Member' before the
    person commits to a password. Doesn't leak the full invite record.
    """
    code = request.query_params.get("code", "").strip()
    if not code:
        return Response(
            {"detail": "Missing ?code query parameter."}, status=status.HTTP_400_BAD_REQUEST
        )
    invite = InviteCode.objects.filter(code=code).first()
    if invite is None or not invite.is_redeemable():
        # Don't reveal whether the code exists-but-expired vs never-existed.
        # Both are dead from the invitee's perspective.
        return Response(
            {"detail": "Invite code is not valid or has expired."},
            status=status.HTTP_404_NOT_FOUND,
        )
    return Response(
        {
            "intended_label": invite.intended_label,
            "intended_group_names": [g.name for g in invite.intended_groups.all()],
            "expires_at": invite.expires_at,
        },
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def redeem_invite_code(request):
    """Anonymous: create a fresh User + add to intended_groups, mark code used.

    Single-use; expires_at is enforced; intended_groups drives Django
    permission + ForgeKey door access (per the existing group-derived
    rule).
    """
    from django.contrib.auth import get_user_model
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError as DjangoValidationError
    from django.db import IntegrityError, transaction

    serializer = InviteCodeRedeemSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    try:
        validate_password(data["password"])
    except DjangoValidationError as exc:
        return Response({"password": list(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)

    User = get_user_model()

    # Lock the invite row for the duration of redemption so two concurrent
    # POSTs of the same code can't both create users.
    with transaction.atomic():
        invite = InviteCode.objects.select_for_update().filter(code=data["code"]).first()
        if invite is None or not invite.is_redeemable():
            return Response(
                {"detail": "Invite code is not valid or has expired."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = User.objects.create_user(
                username=data["username"],
                email=data["email"],
                password=data["password"],
                first_name=data.get("first_name", ""),
                last_name=data.get("last_name", ""),
            )
        except IntegrityError:
            return Response(
                {"username": ["A user with that username or badge already exists."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Apply group membership BEFORE marking redeemed so a failure rolls
        # the whole transaction back (no orphan user, no half-redeemed code).
        for group in invite.intended_groups.all():
            user.groups.add(group)

        invite.redeemed = True
        invite.redeemed_at = timezone.now()
        invite.redeemed_by = user
        invite.redeemed_ip = _client_ip(request)
        invite.save(update_fields=["redeemed", "redeemed_at", "redeemed_by", "redeemed_ip"])

    return Response(
        {
            "message": "Account created. You can now sign in.",
            "username": user.username,
        },
        status=status.HTTP_201_CREATED,
    )
