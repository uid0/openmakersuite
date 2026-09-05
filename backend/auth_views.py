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

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from config.api_errors import ErrorCode, error_response
from config.tokens import CustomRefreshToken
from notifications.device_login import set_device_cookie, track_device_login

User = get_user_model()


def _tokens_for(user):
    """Return (access, refresh) JWTs for the given user."""
    refresh = CustomRefreshToken.for_user(user)
    return str(refresh.access_token), str(refresh)


def _renew_session_from_token(request, access):
    """Re-establish the Django session for the user a fresh access token names.

    Called by :func:`refresh_token`; see its docstring for why the session has
    to track the JWT's life rather than its own. Best effort by design — the
    caller has already proven possession of a valid refresh token, so nothing
    here can widen access, and a caller with no session support (ScanTTY, a
    script) simply gets no cookie, as before.
    """
    try:
        user = JWTAuthentication().get_user(access)
    except Exception:
        # Resolved by the SAME code that resolves a Bearer token on any other
        # request, so the session can never name a user the API would refuse.
        return
    if request.session.get("_auth_user_id") == str(user.pk):
        # Already this user's session: cycling the key would invalidate the
        # cookie the browser is holding. Re-save so the expiry moves forward.
        request.session.modified = True
        return
    django_login(request, user)


def _issue_session_and_tokens(request, user):
    """
    Create a Django session for ``user`` and return a login payload that also
    includes JWT tokens. A single call logs the user into the frontend (JWT),
    the DRF browsable API (session), and the Django admin (session).

    This is the single chokepoint both /api/auth/login/ and /api/auth/register/
    route through, so known-device tracking hooks in here, right after
    ``django_login``. The device token to persist on the response cookie is
    stashed on ``request`` for the caller to apply via
    :func:`_attach_device_cookie` — the cookie must be set on the ``Response``
    the view builds, not on this payload dict. Tracking never raises.
    """
    django_login(request, user)
    request._oms_device_token = track_device_login(request, user)
    access, refresh = _tokens_for(user)
    return {
        "access": access,
        "refresh": refresh,
        "username": user.username,
        "email": user.email,
        "is_staff": user.is_staff,
        "is_superuser": user.is_superuser,
    }


def _attach_device_cookie(request, response):
    """
    Persist the device cookie stashed by :func:`_issue_session_and_tokens`
    onto ``response``, if one was minted. Returns ``response`` for chaining.
    """
    token = getattr(request, "_oms_device_token", None)
    if token:
        set_device_cookie(response, token)
    return response


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
        return error_response(ErrorCode.VALIDATION_FAILED, "Username is required")

    if len(username) < 3:
        return error_response(
            ErrorCode.VALIDATION_FAILED,
            "Username must be at least 3 characters long",
        )

    # Check if username already exists
    if User.objects.filter(username=username).exists():
        return error_response(
            ErrorCode.CONFLICT,
            "Username already exists. Please choose another.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # Validate email if provided
    if email and not re.match(r"^[^@]+@[^@]+\.[^@]+$", email):
        return error_response(
            ErrorCode.VALIDATION_FAILED,
            "Please enter a valid email address",
        )

    try:
        user = User.objects.create_user(username=username, email=email, password=password)
        user.save()

        payload = _issue_session_and_tokens(request, user)
        payload["detail"] = "User created successfully"
        response = Response(payload, status=status.HTTP_201_CREATED)
        return _attach_device_cookie(request, response)

    except Exception as e:
        return error_response(
            ErrorCode.SERVER_ERROR,
            f"Registration failed: {str(e)}",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
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
        return error_response(
            ErrorCode.VALIDATION_FAILED,
            "Username and password are required",
        )

    user = authenticate(request, username=username, password=password)

    if user is None:
        return error_response(
            ErrorCode.AUTHENTICATION_FAILED,
            "Invalid credentials",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    if not user.is_active:
        return error_response(
            ErrorCode.AUTHENTICATION_FAILED,
            "User account is disabled",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    if not user.can_login():
        return error_response(
            ErrorCode.PERMISSION_DENIED,
            "User does not have an active membership or required role",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    response = Response(_issue_session_and_tokens(request, user))
    return _attach_device_cookie(request, response)


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
    Refresh JWT access token, and re-establish the Django session with it.

    THE SESSION IS RENEWED HERE BECAUSE /media/ RUNS ON IT
    (op-anonymous-read-posture). ``config.protected_media`` gates the vendor
    prefixes on ``request.user.is_authenticated``, which a browser satisfies
    with the session cookie ``login_user`` sets — a ``<a href="/media/...">``
    carries no ``Authorization`` header. But the SPA runs on the JWT, whose
    refresh lifetime is ``SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"]`` (30 days),
    while the session cookie expires at Django's default ``SESSION_COOKIE_AGE``
    (14 days) and is not slid forward, because ``SESSION_SAVE_EVERY_REQUEST``
    is left at ``False``. So an operator who signed in once kept working
    through the API for 30 days and, from day 15, got a bare 403 on every
    supplier agreement, invoice and receipt.

    Renewing here ties the cookie's life to the credential the app actually
    runs on: the SPA refreshes to stay signed in, and the session it needs for
    a file download is renewed by the same call.

    TWO OTHER FIXES WERE REJECTED, and this note exists so neither is
    "simplified" back in later: lengthening ``SESSION_COOKIE_AGE`` (or setting
    ``SESSION_SAVE_EVERY_REQUEST``) extends a credential's lifetime, and
    teaching the media gate to accept a JWT changes what the gate accepts.
    Both are security-posture decisions and belong to the captain, not to a
    regression fix.

    A failed renewal does NOT fail the refresh: the access token is what the
    caller asked for, and a session is a bonus that a caller without a cookie
    (ScanTTY, curl) never had. ``config/tests/test_media_session_lifetime.py``
    exercises the sequence this exists for.
    """
    refresh_token = request.data.get("refresh")

    if not refresh_token:
        return error_response(ErrorCode.VALIDATION_FAILED, "Refresh token is required")

    try:
        refresh = CustomRefreshToken(refresh_token)
        access = refresh.access_token

        _renew_session_from_token(request, access)

        return Response(
            {
                "access": str(access),
            }
        )

    except Exception:
        return error_response(
            ErrorCode.AUTHENTICATION_FAILED,
            "Invalid refresh token",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )


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
        return error_response(
            ErrorCode.PERMISSION_DENIED,
            "This endpoint is only available in DEBUG mode",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    username = request.data.get("username", "").strip()
    if not username:
        return error_response(ErrorCode.VALIDATION_FAILED, "Username is required")

    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        return error_response(
            ErrorCode.NOT_FOUND,
            f"User '{username}' not found",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # E2E suite drives admin-only flows (e.g., LocationViewSet.create requires
    # IsAdminUser) through this helper, so it also has to be able to promote
    # the user to staff in DEBUG mode.
    if request.data.get("is_staff"):
        user.is_staff = True
        user.save(update_fields=["is_staff"])

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
            "is_staff": user.is_staff,
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def create_test_invite_code(request):
    """Mint an InviteCode for E2E tests. DEBUG-only.

    Mirrors `create_test_membership` so Playwright can seed an open
    invite without holding admin credentials. Body: `{label?: str,
    expires_in_days?: int}`. Returns `{code, redeem_url}`.
    """
    from django.conf import settings
    from django.utils import timezone

    from membership.models import InviteCode

    if not settings.DEBUG:
        return error_response(
            ErrorCode.PERMISSION_DENIED,
            "This endpoint is only available in DEBUG mode",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    label = (request.data.get("label") or "E2E test invite").strip()
    try:
        days = int(request.data.get("expires_in_days") or 7)
    except (TypeError, ValueError):
        days = 7
    expires_at = timezone.now() + timezone.timedelta(days=days)
    invite = InviteCode.objects.create(
        code=InviteCode.generate_code(),
        intended_label=label,
        expires_at=expires_at,
    )
    return Response(
        {
            "code": invite.code,
            "intended_label": invite.intended_label,
            "expires_at": invite.expires_at.isoformat(),
            "redeem_url": f"/invite/{invite.code}",
        },
        status=status.HTTP_201_CREATED,
    )
