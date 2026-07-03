"""Auth for the interlocks API.

Two audiences with very different trust levels:

* **Staff/admin operators** manage interlock records through the browsable /
  JWT API — gated by :class:`IsStaffUser` (mirrors
  ``inventory.safety_sheet.IsStaffUser``).
* **The Pi executor daemon** polls the command-queue (which returns *decrypted*
  SSH credentials) and reports results. It has no Django user, so it presents a
  dedicated shared token in the ``X-Interlock-Token`` header, checked by
  :class:`InterlockDaemonTokenAuthentication` + :class:`IsInterlockDaemon`.

The daemon path is deliberately NOT ``AllowAny`` (unlike the print-queue) —
it exposes plaintext creds, so it must authenticate. It is also fail-closed:
if ``INTERLOCK_DAEMON_TOKEN`` is unset, every request is rejected.
"""

from __future__ import annotations

from django.conf import settings
from django.utils.crypto import constant_time_compare

from rest_framework import authentication, exceptions
from rest_framework.permissions import BasePermission

# Header the Pi executor presents its shared token in. A dedicated header (not
# ``Authorization: Bearer``) keeps daemon auth from colliding with JWT auth.
DAEMON_TOKEN_HEADER = "HTTP_X_INTERLOCK_TOKEN"
# Marker stored on ``request.auth`` when the daemon token validates.
DAEMON_AUTH_MARKER = "interlock-daemon"


class IsStaffUser(BasePermission):
    """Staff-only gate for operator-facing interlock management."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_staff)


class _InterlockDaemonPrincipal:
    """A minimal non-Django principal for an authenticated Pi executor.

    Not a real user (the daemon has no account); it only needs to satisfy
    DRF's ``is_authenticated`` check so downstream code treats the request as
    authenticated.
    """

    is_authenticated = True
    is_active = True

    def __str__(self) -> str:  # pragma: no cover - trivial
        return "interlock-daemon"


class InterlockDaemonTokenAuthentication(authentication.BaseAuthentication):
    """Authenticate the Pi executor by a shared token in ``X-Interlock-Token``.

    * No header → ``None`` (unauthenticated; the permission then yields 401).
    * Header present but token unconfigured or mismatched → ``AuthenticationFailed``
      (401). Compared in constant time so a wrong token leaks no timing signal.
    """

    def authenticate(self, request):
        provided = request.META.get(DAEMON_TOKEN_HEADER, "")
        if not provided:
            return None
        expected = (getattr(settings, "INTERLOCK_DAEMON_TOKEN", "") or "").strip()
        if not expected or not constant_time_compare(provided, expected):
            raise exceptions.AuthenticationFailed("Invalid interlock daemon token.")
        return (_InterlockDaemonPrincipal(), DAEMON_AUTH_MARKER)

    def authenticate_header(self, request):
        # Presence of this header makes DRF return 401 (not 403) when auth is
        # missing on a protected endpoint.
        return "X-Interlock-Token"


class IsInterlockDaemon(BasePermission):
    """Allow only requests carrying a valid interlock daemon token."""

    def has_permission(self, request, view):
        return getattr(request, "auth", None) == DAEMON_AUTH_MARKER
