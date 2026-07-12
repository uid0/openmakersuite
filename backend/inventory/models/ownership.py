"""Shared ownership abstraction for ``Asset`` and ``InventoryItem`` (#881).

Both models can be owned by a User, a Group (SIG), or the Space (the
makerspace itself). The fields and the small permission-delegation methods
were byte-identical duplicates on the two models; this abstract base is now
their single home.

Field names, types, choices, defaults and ``on_delete`` are unchanged from the
pre-#881 per-model definitions. The only reconciled differences are:

* ``related_name`` — the two models used distinct reverse accessors
  (``owned_assets`` / ``owned_inventory_items``). A single inherited field
  can't reproduce both, so the mixin uses the interpolated
  ``%(app_label)s_%(class)s_owned`` pattern (yielding ``inventory_asset_owned``
  / ``inventory_inventoryitem_owned``). Nothing in the codebase reads these
  reverse accessors, so the rename is invisible.
* ``help_text`` — collapsed to a single generic string per field.

Neither ``related_name`` nor ``help_text`` is a database column, so the
resulting migration is *state-only* (``sqlmigrate`` emits no schema DDL).

The ``is_user_*`` methods delegate to :mod:`membership.services` (the single
source of truth for ownership auth); they are defined once here instead of
being duplicated on each model.
"""

from django.conf import settings
from django.contrib.auth.models import Group
from django.db import models


class OwnableModel(models.Model):
    """Abstract base contributing ownership fields + permission helpers."""

    class OwnershipType(models.TextChoices):
        USER = "user", "User"
        GROUP = "group", "Group"
        SPACE = "space", "Space"

    ownership_type = models.CharField(
        max_length=10,
        choices=OwnershipType.choices,
        default=OwnershipType.SPACE,
        help_text="Type of ownership for this record (user, group, or space)",
    )
    owning_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="%(app_label)s_%(class)s_owned",
        help_text="User that owns this record (if applicable)",
    )
    owning_group = models.ForeignKey(
        Group,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="%(app_label)s_%(class)s_owned",
        help_text="Group (SIG) that owns this record (if applicable)",
    )

    class Meta:
        abstract = True

    def is_user_admin(self, user) -> bool:
        """Check if user is a system admin (staff or superuser)."""
        from membership.services import is_system_admin

        return is_system_admin(user)

    def is_user_in_logistics(self, user) -> bool:
        """Check if user is in the Logistics group."""
        from membership.services import is_logistics_member

        return is_logistics_member(user)

    def is_user_group_admin(self, user) -> bool:
        """Check if user is a SIG admin for this record's owning group."""
        from membership.services import is_owning_group_admin

        return is_owning_group_admin(user, self.owning_group)
