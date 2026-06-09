"""Permission helpers for the storage_vision app.

All write paths require a staff or Logistics user (AC-4, AC-7). Reads
remain authenticated-user-only — the setup tables don't include secrets
once the camera token is hashed, but they enumerate slot → item links
which we don't want to leak to anonymous callers.
"""

from __future__ import annotations

from rest_framework import permissions

from membership.utils import is_logistics_member


def _is_staff_or_logistics(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    return user.is_staff or user.is_superuser or is_logistics_member(user)


class IsStaffOrLogistics(permissions.BasePermission):
    """Authenticated staff / superuser / Logistics group member."""

    def has_permission(self, request, view) -> bool:
        return _is_staff_or_logistics(request.user)


class IsStaffOrLogisticsOrReadOnly(permissions.BasePermission):
    """Same as :class:`IsStaffOrLogistics` for unsafe methods; safe methods
    require any authenticated user (no anonymous reads on this app)."""

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return _is_staff_or_logistics(user)
