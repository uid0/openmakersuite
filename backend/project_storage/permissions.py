"""Permission classes for project storage.

The warden surfaces (list, retrieve, by-member lookup) gate to staff users
*or* members of the ``Storage Admin`` Django group. The latter lets the
shop owner delegate sweep-day duties to volunteer wardens without
elevating them to platform-wide ``is_staff``. Mutating actions
(send-notice, move-to-purgatory, mark-removed) keep the tighter
``IsAdminUser`` gate because they touch member-facing email + can
write off a member's stored work.

The kiosk and Pi-daemon endpoints (start, label, print_queue,
mark_printed) remain ``AllowAny`` — see ``views.py::get_permissions``.
"""

from __future__ import annotations

from rest_framework import permissions

STORAGE_ADMIN_GROUP = "Storage Admin"


class IsStorageAdminOrStaff(permissions.BasePermission):
    """Allow read access to staff/superuser OR members of the
    ``Storage Admin`` group. Anonymous callers are rejected.
    """

    message = "You must be staff or a Storage Admin to view project-storage stints."

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_staff or user.is_superuser:
            return True
        return user.groups.filter(name=STORAGE_ADMIN_GROUP).exists()
