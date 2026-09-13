"""Refusing a supplier-link write made from a stale copy (optimistic concurrency).

THE DEFECT THIS CLOSES
----------------------

Two people open the same :class:`~inventory.models.ItemSupplier`. One saves a
new lead time of 12; the other then saves their form, which still holds the 7
it loaded, and the 12 is gone — a real supplier quote lost with no word to
either of them. Every field of the row behaves this way, not only the lead time.

THE TOKEN: ``ItemSupplier.version``
-----------------------------------

An integer that EVERY write to the row moves on by one, read and written under
a row lock inside ``ItemSupplier.save()``. A caller that loaded the row at
version ``N`` states it by setting ``expected_version = N`` on the instance
before saving; the save is refused with :class:`StaleSupplierLink` when the row
on disk is no longer at ``N``.

Why a counter rather than ``updated_at``, which the model already has: two of
the writes that must make a copy stale do not move ``updated_at``. The measuring
task saves with ``update_fields=["average_lead_time"]``, and Django only writes
an ``auto_now`` column that is named in ``update_fields``; demoting a sibling
primary is a ``QuerySet.update()``, which never touches it. A timestamp token
would also have to survive a round trip through JSON and an HTML hidden field
at microsecond precision. A counter has neither problem, and the migration that
adds it gives every existing row ``1`` without rewriting the table.

WHY THE CHECK CANNOT RACE THE WRITE
-----------------------------------

:func:`lock_item_supplier_links` is the first lock taken by every supplier-link
write. On PostgreSQL it takes one transaction-scoped advisory lock per item, so
creates, updates, deletes, primary arbitration and existence checks serialize
before any caller takes an ``ItemSupplier`` row lock. It is a no-op on other
databases. One ordering rule prevents a row-lock/advisory-lock inversion while
also providing a shared boundary when an item has no links yet.

:func:`claim_version` takes ``SELECT ... FOR UPDATE`` on the row inside the
save's transaction, compares, and bumps. A second save of the same row blocks
on that lock until the first commits and then reads the committed version, so
two saves made from the same load cannot both pass the check. The lock is taken
on every save, with or without a token, because the bump itself must not race:
two unlocked saves could both write ``N + 1``, and a copy loaded between them
would then pass a check it should fail.

A WRITE THAT CARRIES NO TOKEN IS UNCHANGED
------------------------------------------

``expected_version`` is ``None`` unless a caller sets it, and ``None`` is not
checked: the row is written exactly as before, and still moves the version on.
The API keeps the token optional for that reason — ScanTTY PATCHes
``/api/inventory/item-suppliers/`` and ships separately, so a client that does
not know about ``version`` must keep working. A tokenless write is therefore
still last-write-wins; closing that is each client's adoption of the token.

WHO SENDS A TOKEN, AND HOW A REFUSAL REACHES A PERSON
-----------------------------------------------------

* ``PATCH``/``PUT /api/inventory/item-suppliers/{id}/`` with ``version`` —
  ``409`` with ``error.code == "stale_version"`` (``docs/API_ERROR_CONTRACT.md``).
  The web item form always sends the version it loaded and tells the operator
  their copy is out of date, with a reload.
* ``DELETE /api/inventory/item-suppliers/{id}/?version=N`` — the same refusal;
  the check and deletion share one row lock. The web item form sends the token.
* ``PATCH``/``PUT /api/inventory/kits/{id}/`` with ``supplier_terms.version`` —
  the same ``409``, and the whole kit save is rolled back with it. A positive
  version means the page loaded that link; zero means an existing kit loaded no
  link for that supplier. A mismatch in either content or existence is stale.
* The Django admin (``ItemSupplierAdmin`` and the item admin's inline) — the
  version the page was rendered with rides in a hidden field and a stale POST
  is refused as a form error.

The standalone admin delete page and bulk delete action carry no token because
their confirmation step reads the current row rather than posting editable
values from an older copy. Inline deletion does carry ``loaded_version`` and is
refused by the same form validation as an inline edit.

Paths that send no token, and why they need none: creates (nothing was loaded);
``_sync_primary_supplier`` (item create only, so the link is always new);
``mark_discontinued`` and voiding a purchase-order line (each loads the row in
the same request and sets two flags the operator just asked for — there is no
earlier copy to be stale); the measuring task (writes a value it computed, not
one it loaded, with ``update_fields``); sibling demotion (a consequence of
another row's save, which moves this row's version on in the same statement).

``QuerySet.update()`` and ``bulk_update()`` do not pass through ``save()``. A
production write made that way must move ``version`` on in the same statement,
as ``inventory.services.suppliers.enforce_single_primary`` does, or copies
loaded before it will not be refused. (Tests seed rows that way on purpose, so
this is a rule for write paths rather than a refusal in the queryset.)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from django.db import connection

if TYPE_CHECKING:
    from inventory.models.core import ItemSupplier

VERSION_FIELD = "version"

#: The refusal's ``error.code`` on the API. Stable: clients switch on it.
STALE_VERSION_CODE = "stale_version"


def lock_item_supplier_links(item_id) -> None:
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            [f"inventory.itemsupplier.links:{item_id}"],
        )


class StaleSupplierLink(Exception):
    """A save stated the version it loaded, and the row has moved on since."""

    def __init__(self, item_supplier_id, sent: int, current: Optional[int]):
        self.item_supplier_id = item_supplier_id
        self.sent = sent
        #: ``None`` when the row has been deleted since it was loaded.
        self.current = current
        super().__init__(item_supplier_id, sent, current)

    def __str__(self) -> str:
        return self.message

    @property
    def message(self) -> str:
        if self.current is None:
            return (
                "This supplier link was deleted after you loaded it, so your changes were "
                "not saved. Reload to see the item's current suppliers."
            )
        return (
            "Someone else changed this supplier link after you loaded it, so your changes "
            "were not saved. Your copy is out of date: reload to see the current values, "
            "then make your change again."
        )

    def details(self) -> dict:
        """The refusal's ``error.details`` on the API."""
        return {
            "id": self.item_supplier_id,
            "sent_version": self.sent,
            "current_version": self.current,
        }


def claim_version(item_supplier: "ItemSupplier", update_fields):
    """Check the caller's token and move the version on; return ``update_fields``.

    Called by ``ItemSupplier.save()`` inside its transaction, BEFORE anything
    else reads the stored row, so the row lock also covers the reads the cost
    derivation and the lead-time source are decided from. Leaves
    ``expected_version`` in place: ``save()`` clears it only once the write has
    succeeded, so a save that fails for another reason and is retried is still
    checked against the load it came from.
    """
    expected = item_supplier.expected_version

    if item_supplier.pk is None:
        return update_fields

    current = (
        type(item_supplier)
        .objects.select_for_update()
        .filter(pk=item_supplier.pk)
        .values_list(VERSION_FIELD, flat=True)
        .first()
    )
    if expected is not None and expected != current:
        raise StaleSupplierLink(item_supplier.pk, expected, current)
    if current is None:
        # No row to move on from (a save that will INSERT under an explicit pk).
        return update_fields

    item_supplier.version = current + 1
    if update_fields is None:
        return None
    return frozenset(update_fields) | {VERSION_FIELD}
