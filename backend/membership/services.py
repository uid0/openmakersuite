"""Ownership & SIG-permission service — the single source of truth (#881).

This module consolidates the ownership/permission auth that previously lived
directly in :mod:`membership.utils` and was duplicated across the
``Asset``/``InventoryItem`` model methods. The behaviour is preserved
bit-for-bit; only the *home* of the logic changed.

* :mod:`membership.utils` re-exports the public gates below for its many
  existing importers, so ``from membership.utils import ...`` keeps working.
* The :class:`inventory.models.ownership.OwnableModel` mixin delegates its
  ``is_user_*`` methods here (see :func:`is_system_admin`,
  :func:`is_logistics_member`, :func:`is_owning_group_admin`).

Certification helpers (``is_certified`` / ``user_active_certifications``) are
*not* ownership auth and remain in :mod:`membership.utils`.
"""

from django.contrib.auth.models import Group

from .models import SIGAdmin


class OwnershipVisibility:
    """Policy controlling how *regular* users (authenticated, but neither
    staff/superuser/Logistics nor a SIG admin) see ownership-scoped rows.

    Staff, superusers, Logistics and SIG admins behave identically under both
    policies; only the regular-user fallthrough differs.
    """

    #: Regular users see every row (no ownership filter). Used by the item and
    #: asset *list* viewsets and the reorder ``pending`` feed.
    LIST = "list"

    #: Regular users see only space-owned rows (``owning_group IS NULL``). Used
    #: by the dashboard widgets.
    RESTRICTED = "restricted"


# ─────────────────────────────────────────────────────────────────────────
# Primitives
# ─────────────────────────────────────────────────────────────────────────
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


def is_system_admin(user) -> bool:
    """Return True if ``user`` is a system admin (staff or superuser).

    This mirrors the historical ``Asset.is_user_admin`` /
    ``InventoryItem.is_user_admin`` bodies exactly (no ``None`` guard — every
    caller passes ``request.user``); the ``OwnableModel`` mixin delegates here.
    """
    return user.is_authenticated and (user.is_staff or user.is_superuser)


def is_owning_group_admin(user, owning_group) -> bool:
    """Return True if ``user`` is a SIG admin of ``owning_group``.

    Mirrors the historical ``is_user_group_admin`` model method: False when the
    user is unauthenticated or there is no owning group. Backs
    ``OwnableModel.is_user_group_admin``.
    """
    if not user.is_authenticated or not owning_group:
        return False
    return is_sig_admin(user, owning_group)


# ─────────────────────────────────────────────────────────────────────────
# Object-management gates (broad — staff/super/Logistics/SIG-admin/space)
# ─────────────────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────────────────
# Assign/reassign-to-SIG write gate (narrower than can_manage_sig_*)
# ─────────────────────────────────────────────────────────────────────────
def can_assign_to_owning_group(user, group) -> bool:
    """Return True if ``user`` may assign an asset/item to SIG ``group``.

    Staff, superusers, Logistics members, and SIG admins of ``group`` may
    assign. This is the shared "assign/reassign to a SIG" write gate used by
    the asset and inventory create/update endpoints — deliberately distinct
    from the broader :func:`can_manage_sig_asset` / :func:`can_manage_sig_inventory`
    object-management gate (which also grants regular users rights over
    space-owned rows). Callers invoke this only after confirming the request
    user is authenticated and ``group`` is a resolved :class:`Group`.
    """
    return bool(
        user.is_superuser or user.is_staff or is_logistics_member(user) or is_sig_admin(user, group)
    )


# ─────────────────────────────────────────────────────────────────────────
# Queryset visibility (parameterized by policy — see OwnershipVisibility)
# ─────────────────────────────────────────────────────────────────────────
def scope_queryset_by_ownership(queryset, user, *, policy, field="owning_group"):
    """Restrict ``queryset`` to the rows ``user`` may see, by SIG ownership.

    Applies the shared visibility rule used across the inventory / dashboard /
    reorder surfaces:

    * unauthenticated, staff, superuser, or Logistics → full queryset
    * SIG admin → rows whose owning group is one they administer
      (``{field}__in=<their SIGs>``)
    * regular authenticated user → depends on ``policy``:

        - :attr:`OwnershipVisibility.LIST` → full queryset (they see everything)
        - :attr:`OwnershipVisibility.RESTRICTED` → space-owned rows only
          (``{field}__isnull=True``)

    ``field`` is the ORM path to the ``owning_group`` FK relative to the
    queryset's model (e.g. ``"owning_group"``, ``"item__owning_group"``,
    ``"asset__owning_group"``), so the same helper scopes both direct and
    related querysets.
    """
    if not user.is_authenticated or user.is_superuser or user.is_staff:
        return queryset
    # Logistics can see everything
    if is_logistics_member(user):
        return queryset
    # SIG admins can only see rows owned by their SIGs
    user_sigs = get_user_managed_sigs(user)
    if user_sigs.exists():
        return queryset.filter(**{f"{field}__in": user_sigs})
    # Regular authenticated user (administers no SIGs)
    if policy == OwnershipVisibility.RESTRICTED:
        return queryset.filter(**{f"{field}__isnull": True})
    return queryset
