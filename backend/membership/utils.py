"""
Permission utilities for SIG (Special Interest Group) management.

The ownership / SIG-permission logic now lives in :mod:`membership.services`
(the single source of truth, #881). The gates are re-exported here so the many
existing ``from membership.utils import ...`` callers keep working unchanged.
Certification helpers are unrelated to ownership auth and remain defined here.
"""

from .services import (  # noqa: F401  (re-exported for existing importers)
    can_create_reorder_request,
    can_manage_sig_asset,
    can_manage_sig_inventory,
    get_user_managed_sigs,
    is_logistics_member,
    is_sig_admin,
    is_staff_or_sig_admin,
)


def is_certified(user, certification) -> bool:
    """Return True if `user` currently holds `certification` (gh #374
    follow-up for ForgeKey locker/door access).

    "Currently" means a UserCertification row exists with `revoked_at`
    null AND the parent Certification is active. Inactive certifications
    do not gate access regardless of grant history (operator can park a
    cert temporarily by toggling `is_active`).

    Staff / superusers / Logistics bypass is handled at the *access*
    layer (e.g. `lockers.services.access.can_user_access_locker`), not
    here — this helper is the literal "do they hold the grant?"
    question.
    """
    if not user or not user.is_authenticated or certification is None:
        return False
    if not certification.is_active:
        return False
    return certification.user_grants.filter(
        user=user,
        revoked_at__isnull=True,
    ).exists()


def user_active_certifications(user):
    """Return the QuerySet of currently-active Certification rows held
    by `user`. Empty queryset for anonymous / unauthenticated callers.
    """
    from .models import Certification  # local to avoid circular import

    if not user or not user.is_authenticated:
        return Certification.objects.none()
    return Certification.objects.filter(
        is_active=True,
        user_grants__user=user,
        user_grants__revoked_at__isnull=True,
    ).distinct()
