"""
Permission utilities for SIG (Special Interest Group) management.
"""

from django.contrib.auth.models import Group

from .models import SIGAdmin


def is_sig_admin(user, group):
    """
    Check if a user is an admin of a specific SIG (Group).

    Args:
        user: The user to check
        group: The Group (SIG) to check

    Returns:
        bool: True if user is an admin of the group, False otherwise
    """
    return SIGAdmin.is_sig_admin(user, group)


def can_manage_sig_asset(user, asset):
    """
    Check if a user can manage a specific asset.

    Users can manage an asset if:
    - They are a system admin (staff/superuser)
    - They are in the Logistics group
    - They are a SIG admin of the asset's owning group
    - The asset is space-owned (no owning_group) and user is authenticated (but not a SIG admin)

    Args:
        user: The user to check
        asset: The asset to check

    Returns:
        bool: True if user can manage the asset, False otherwise
    """
    if not user or not user.is_authenticated:
        return False

    # System admins can manage everything
    if user.is_staff or user.is_superuser:
        return True

    # Logistics can manage everything
    if is_logistics_member(user):
        return True

    # If asset has no owning group (space-owned)
    if not asset.owning_group:
        # Regular authenticated users can manage space-owned assets
        # But SIG admins cannot (they can only manage assets owned by their SIG)
        user_managed_sigs = get_user_managed_sigs(user)
        if user_managed_sigs.exists():
            # User is a SIG admin, so they cannot manage space-owned assets
            return False
        # Regular user can manage space-owned assets
        return True

    # SIG admins can manage assets owned by their SIG
    return is_sig_admin(user, asset.owning_group)


def can_manage_sig_inventory(user, item):
    """
    Check if a user can manage a specific inventory item.

    Users can manage an inventory item if:
    - They are a system admin (staff/superuser)
    - They are in the Logistics group
    - They are a SIG admin of the item's owning group
    - The item is space-owned (no owning_group) and user is authenticated (but not a SIG admin)

    Args:
        user: The user to check
        item: The inventory item to check

    Returns:
        bool: True if user can manage the item, False otherwise
    """
    if not user or not user.is_authenticated:
        return False

    # System admins can manage everything
    if user.is_staff or user.is_superuser:
        return True

    # Logistics can manage everything
    if is_logistics_member(user):
        return True

    # If item has no owning group (space-owned)
    if not item.owning_group:
        # Regular authenticated users can manage space-owned items
        # But SIG admins cannot (they can only manage items owned by their SIG)
        user_managed_sigs = get_user_managed_sigs(user)
        if user_managed_sigs.exists():
            # User is a SIG admin, so they cannot manage space-owned items
            return False
        # Regular user can manage space-owned items
        return True

    # SIG admins can manage inventory items owned by their SIG
    return is_sig_admin(user, item.owning_group)


def get_user_managed_sigs(user):
    """
    Get all SIGs (Groups) that a user can manage.

    Args:
        user: The user to check

    Returns:
        QuerySet: QuerySet of Groups the user administers
    """
    return SIGAdmin.get_user_sigs(user)


def is_logistics_member(user):
    """
    Check if a user is a member of the Logistics group.

    Args:
        user: The user to check

    Returns:
        bool: True if user is in Logistics group, False otherwise
    """
    if not user or not user.is_authenticated:
        return False

    try:
        logistics_group = Group.objects.get(name="Logistics")
        return logistics_group in user.groups.all()
    except Group.DoesNotExist:
        return False


def can_create_reorder_request(user, item):
    """
    Check if a user can create a reorder request for an inventory item.

    Users can create reorder requests if:
    - They are a system admin (staff/superuser)
    - They are in the Logistics group (always allowed)
    - The item is requestable and they are a SIG admin of the item's owning group
    - The item is requestable and has no owning group (space-owned)

    Args:
        user: The user to check
        item: The inventory item to check

    Returns:
        bool: True if user can create reorder request, False otherwise
    """
    if not user or not user.is_authenticated:
        return False

    # System admins and Logistics can always create reorder requests
    if user.is_staff or user.is_superuser or is_logistics_member(user):
        return True

    # Item must be requestable
    if not item.is_requestable:
        return False

    # If item has no owning group (space-owned), any authenticated user can request
    if not item.owning_group:
        return True

    # SIG admins can create reorder requests for their SIG's inventory
    return is_sig_admin(user, item.owning_group)


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


def is_staff_or_sig_admin(user) -> bool:
    """Return True if the user is staff/superuser/Logistics or a SIG admin
    of any SIG.

    Used to gate maintenance work order writes (gh #374): the operator-set
    rule is that staff and SIG leaders can add or modify work orders, while
    other volunteers can only read non-third-party work orders. Logistics
    has staff-equivalent reach in this codebase, so it is included alongside
    staff for parity with `can_manage_sig_asset` / `can_manage_sig_inventory`.

    Note: this is intentionally a *role* check, not a per-asset/per-SIG
    check. A SIG admin may modify any work order, not just those owned by
    their SIG. If finer scoping is needed later, extend the workflow
    transitions in `maintenance_orders.transitions`.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    if is_logistics_member(user):
        return True
    return get_user_managed_sigs(user).exists()
