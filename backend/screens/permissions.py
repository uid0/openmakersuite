"""
Permission helpers for the screens app.
"""

from membership.utils import get_user_managed_sigs, is_logistics_member


def user_can_manage_screen(user, screen) -> bool:
    """
    Return True if `user` is allowed to manage `screen`.

    Staff / superusers / logistics members have full access. Otherwise the
    user must be an admin of the owning SIG.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff or is_logistics_member(user):
        return True
    if screen.sig_id is None:
        return False
    return get_user_managed_sigs(user).filter(pk=screen.sig_id).exists()


def user_can_manage_system_messages(user) -> bool:
    """Only staff / superusers / logistics may manage system-wide messages."""
    if not user or not user.is_authenticated:
        return False
    return user.is_superuser or user.is_staff or is_logistics_member(user)
