"""DRF permissions for analytics endpoints.

Two access modes:

1. **Authenticated role gate** — staff, superuser, Logistics, or any SIG
   admin (matches the WO-write rule from gh #374).
2. **Signed-URL bypass** — a valid ``?token=<signed>`` query parameter
   lets even unauthenticated callers read the dashboard. Used to share
   the analytics view with board members who don't have OMS accounts.
   Tokens are minted by the staff-gated ``/api/analytics/share/`` endpoint.

The token bypass applies only to read endpoints. Writes (e.g. minting a
new token) still require the authenticated role gate.
"""

from rest_framework.permissions import BasePermission

from membership.utils import is_staff_or_sig_admin

from .services.share_token import InvalidShareToken, verify_share_token


def _has_valid_share_token(request) -> bool:
    token = request.query_params.get("token")
    if not token:
        return False
    try:
        verify_share_token(token)
    except InvalidShareToken:
        return False
    return True


class IsAnalyticsViewer(BasePermission):
    """Authenticated staff/SIG admin OR a valid signed share token."""

    def has_permission(self, request, view):
        if is_staff_or_sig_admin(request.user):
            return True
        return _has_valid_share_token(request)


class IsAnalyticsSharer(BasePermission):
    """Only authenticated staff / SIG admins may mint share tokens."""

    def has_permission(self, request, view):
        return is_staff_or_sig_admin(request.user)
