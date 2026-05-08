"""DRF permission classes built on top of `membership.utils`.

Centralised so multiple apps (work orders, third-party work orders, etc.)
can share the same role gate without re-implementing it inline.
"""

from rest_framework.permissions import BasePermission, SAFE_METHODS

from .utils import is_staff_or_sig_admin


class IsStaffOrSigAdmin(BasePermission):
    """Allow only staff/superuser/Logistics or a SIG admin of any SIG.

    Both read and write are gated. Use this when even the read surface
    must be restricted (e.g. third-party work orders, gh #374).
    """

    def has_permission(self, request, view):
        return is_staff_or_sig_admin(request.user)


class IsAuthenticatedOrStaffSigAdminWrite(BasePermission):
    """Read for any authenticated user; write for staff/SIG admins only.

    Mirrors the rule for the standard maintenance work order pipeline (gh
    #374): all volunteers can see open + completed work orders, but only
    staff and SIG leaders can create or modify them.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            return True
        return is_staff_or_sig_admin(request.user)
