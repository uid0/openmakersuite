"""DRF permissions for analytics endpoints.

Reuses ``membership.is_staff_or_sig_admin`` for the role gate so analytics
follows the same access surface as work-order writes (gh #374). PR2 will
extend this with a signed-URL bypass for board members without OMS
accounts.
"""

from rest_framework.permissions import BasePermission

from membership.utils import is_staff_or_sig_admin


class IsAnalyticsViewer(BasePermission):
    """Allow staff, superuser, Logistics, or any SIG admin to view analytics."""

    def has_permission(self, request, view):
        return is_staff_or_sig_admin(request.user)
