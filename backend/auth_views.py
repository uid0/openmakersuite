"""
Custom authentication views for makerspace users.

The login/register endpoints issue JWT tokens for API clients *and* establish a
Django session, so a single sign-in authenticates the user across the REST API,
the DRF browsable API, and the Django admin.
"""

import re

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout

User = get_user_model()

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from config.tokens import CustomRefreshToken


def _tokens_for(user):
    """Return (access, refresh) JWTs for the given user."""
    refresh = CustomRefreshToken.for_user(user)
    return str(refresh.access_token), str(refresh)


def _issue_session_and_tokens(request, user):
    """
    Create a Django session for ``user`` and return a login payload that also
    includes JWT tokens. A single call logs the user into the frontend (JWT),
    the DRF browsable API (session), and the Django admin (session).
    """
    django_login(request, user)
    access, refresh = _tokens_for(user)
    return {
        "access": access,
        "refresh": refresh,
        "username": user.username,
        "email": user.email,
        "is_staff": user.is_staff,
        "is_superuser": user.is_superuser,
    }


@api_view(["POST"])
@permission_classes([AllowAny])
def register_user(request):
    """
    Register a new user with simple validation.
    For makerspace use - simplified registration process.
    """
    username = request.data.get("username", "").strip()
    email = request.data.get("email", "").strip()
    password = request.data.get("password", "makerspace123")  # Default password

    # Basic validation
    if not username:
        return Response({"detail": "Username is required"}, status=status.HTTP_400_BAD_REQUEST)

    if len(username) < 3:
        return Response(
            {"detail": "Username must be at least 3 characters long"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Check if username already exists
    if User.objects.filter(username=username).exists():
        return Response(
            {"detail": "Username already exists. Please choose another."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Validate email if provided
    if email and not re.match(r"^[^@]+@[^@]+\.[^@]+$", email):
        return Response(
            {"detail": "Please enter a valid email address"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        user = User.objects.create_user(username=username, email=email, password=password)
        user.save()

        payload = _issue_session_and_tokens(request, user)
        payload["detail"] = "User created successfully"
        return Response(payload, status=status.HTTP_201_CREATED)

    except Exception as e:
        return Response(
            {"detail": f"Registration failed: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["POST"])
@permission_classes([AllowAny])
def login_user(request):
    """
    Unified login: authenticate the user, create a Django session, and return
    JWT tokens. The session cookie means the same credentials also grant access
    to the Django admin and the DRF browsable API.
    """
    username = request.data.get("username", "").strip()
    password = request.data.get("password", "")

    if not username or not password:
        return Response(
            {"detail": "Username and password are required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user = authenticate(request, username=username, password=password)

    if user is None:
        return Response({"detail": "Invalid credentials"}, status=status.HTTP_401_UNAUTHORIZED)

    if not user.is_active:
        return Response({"detail": "User account is disabled"}, status=status.HTTP_401_UNAUTHORIZED)

    if not user.can_login():
        return Response(
            {"detail": "User does not have an active membership or required role"},
            status=status.HTTP_403_FORBIDDEN,
        )

    return Response(_issue_session_and_tokens(request, user))


@api_view(["POST"])
@permission_classes([AllowAny])
def logout_user(request):
    """
    Unified logout: destroy the Django session so the user is signed out of the
    admin and the DRF browsable API. JWT tokens are client-held; the frontend
    is expected to discard them.
    """
    django_logout(request)
    return Response({"detail": "Logged out"}, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([AllowAny])
def refresh_token(request):
    """
    Refresh JWT access token.
    """
    refresh_token = request.data.get("refresh")

    if not refresh_token:
        return Response({"detail": "Refresh token is required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        refresh = CustomRefreshToken(refresh_token)
        access = refresh.access_token

        return Response(
            {
                "access": str(access),
            }
        )

    except Exception:
        return Response({"detail": "Invalid refresh token"}, status=status.HTTP_401_UNAUTHORIZED)


@api_view(["POST"])
@permission_classes([AllowAny])
def create_test_membership(request):
    """
    Test helper endpoint to create an active membership for a user.
    Only available in DEBUG mode for E2E testing.
    """
    from django.conf import settings

    from membership.models import Membership

    if not settings.DEBUG:
        return Response(
            {"detail": "This endpoint is only available in DEBUG mode"},
            status=status.HTTP_403_FORBIDDEN,
        )

    username = request.data.get("username", "").strip()
    if not username:
        return Response({"detail": "Username is required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        return Response(
            {"detail": f"User '{username}' not found"}, status=status.HTTP_404_NOT_FOUND
        )

    # Create an active membership for the user
    membership = Membership.objects.create(
        membership_type=Membership.MEMBERSHIP_TYPE_MONTHLY,
        status=Membership.STATUS_ACTIVE,
    )
    membership.users.add(user)

    return Response(
        {
            "detail": f"Active membership created for {username}",
            "membership_id": membership.id,
            "username": username,
        },
        status=status.HTTP_201_CREATED,
    )
