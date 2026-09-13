"""Writing several supplier links of one item as ONE all-or-nothing change.

THE DEFECT THIS CLOSES
----------------------

An item links supplier X on row A and supplier Y on row B. The operator
exchanges them: A becomes Y, B becomes X. Written one row at a time there is no
order that works — whichever row goes first lands on a pair the other row still
holds, ``(item, supplier)`` is unique, and every retry repeats the same order
and fails in the same place. The same dead end meets a new link that claims the
supplier an existing link is moving away from. The only way out used to be two
saves: free the pair by removing a row, then add it back.

Deleting and re-creating inside one request is deliberately NOT the fix. The
link's id is what its price history and the purchase-order lines hang from, so
a delete that lands without its re-create loses them — a failed swap becomes
lost data, which is worse than the dead end.

THE CAPABILITY: one transaction, pair check at its end
------------------------------------------------------

:func:`write_supplier_links` applies every create and update the caller sends
for one item inside one transaction, with the ``(item, supplier)`` constraint
deferred until every write has run. The constraint is ``DEFERRABLE INITIALLY
IMMEDIATE`` (``ItemSupplier.Meta.constraints``), so every OTHER write is still
checked at the end of its own statement exactly as before; only this function
says ``SET CONSTRAINTS ... DEFERRED``, and it says ``IMMEDIATE`` again before
leaving the transaction, so a real collision is refused here, as a 400, and
rolls back every row with it rather than surfacing at commit.

Why a deferred constraint rather than a two-phase write that parks rows on a
placeholder: ``item`` and ``supplier`` are both non-null foreign keys, so a
parked row would need a fake supplier (or item) to exist and would be visible
to every concurrent reader for the length of the transaction. PostgreSQL's
deferred unique check expresses "the pairs must be unique once the change is
complete" directly, and nothing else about the rows changes: ids, price
history, versions and every ``save()`` side effect are the ones a single-link
PATCH produces. On a database without deferrable constraints the exchange is
refused as a collision; nothing is half-written.

THE CONTRACT
------------

* **Lock first, the same rule as every link write.** The item's advisory lock
  (``link_version.lock_item_supplier_links``), then every link row of the item
  ``FOR UPDATE`` in primary-key order.
* **Every version checked before anything is written.** An entry that carries a
  ``version`` is compared against its row under that lock; if any entry is stale
  the whole request is refused with :class:`StaleSupplierLink` for the first
  stale entry in request order, and nothing is written. The saves then run
  without a token of their own: the check has already been made for all of
  them at once, under the lock they are still holding. Each save still moves its
  row's version on. An entry without a ``version`` is unchecked, as on the
  single-link endpoints.
* **The end state is judged before writing.** Rows the request does not name
  keep their supplier; if two links would end up on one supplier, the offending
  entries are refused with DRF's own unique-together sentence, so a client that
  already recognises that rejection reads this one the same way.
* **At most one entry may be promoted**, and it is saved last, so the
  single-primary demotion it causes (``suppliers.enforce_single_primary``) is
  the batch's final word rather than something an earlier entry's save could
  overwrite.

Removals are not part of it. A removal can only free a pair, never take one, so
it is never what makes an exchange impossible, and the single-link DELETE with
``?version=N`` already refuses a stale one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from django.db import IntegrityError, connection, transaction

from inventory.services.link_version import StaleSupplierLink, lock_item_supplier_links

if TYPE_CHECKING:
    from inventory.models.core import InventoryItem, ItemSupplier

#: ``ItemSupplier``'s ``(item, supplier)`` unique constraint. Named so this
#: module can defer it; ``DEFERRABLE INITIALLY IMMEDIATE`` in the schema.
SUPPLIER_PAIR_CONSTRAINT = "inventory_itemsupplier_item_supplier_uniq"

#: The sentence a pair collision is refused with — DRF's own
#: ``UniqueTogetherValidator`` wording, which the web form already recognises.
PAIR_TAKEN_MESSAGE = "The fields item, supplier must make a unique set."


@dataclass
class LinkChange:
    """One entry of a batch: an update when ``id`` is set, else a create."""

    id: Optional[int]
    version: Optional[int]
    #: Validated model field values to set, as a serializer would pass them.
    fields: dict[str, Any] = field(default_factory=dict)


class SupplierLinkBatchRefused(Exception):
    """Entries that cannot be written, by request index; nothing was written."""

    def __init__(self, errors: dict[int, dict[str, list[str]]]):
        self.errors = errors
        super().__init__(errors)


def _supplier_pk(change: LinkChange, row: Optional["ItemSupplier"]):
    supplier = change.fields.get("supplier")
    if supplier is not None:
        return getattr(supplier, "pk", supplier)
    return row.supplier_id if row is not None else None


def _set_pair_check(mode: str) -> None:
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(
            f"SET CONSTRAINTS {connection.ops.quote_name(SUPPLIER_PAIR_CONSTRAINT)} {mode}"
        )


def write_supplier_links(item: "InventoryItem", changes: list[LinkChange]) -> list["ItemSupplier"]:
    """Apply ``changes`` to ``item``'s links atomically; return them in request order.

    Raises :class:`StaleSupplierLink` when any entry's ``version`` is no longer
    current, and :class:`SupplierLinkBatchRefused` when any entry cannot be
    written as asked. Either way — and on any other failure part-way through —
    every link is left exactly as it was.
    """
    from inventory.models.core import ItemSupplier

    try:
        with transaction.atomic():
            lock_item_supplier_links(item.pk)
            rows = {
                row.pk: row
                for row in ItemSupplier.objects.select_for_update().filter(item=item).order_by("pk")
            }

            _refuse_stale(changes, rows)
            _refuse_unwritable(changes, rows)

            # The promotion last: its demotion of the siblings is then final.
            order = sorted(
                range(len(changes)), key=lambda index: bool(changes[index].fields.get("is_primary"))
            )
            written: dict[int, int] = {}
            _set_pair_check("DEFERRED")
            for index in order:
                change = changes[index]
                instance = rows[change.id] if change.id is not None else ItemSupplier(item=item)
                for name, value in change.fields.items():
                    setattr(instance, name, value)
                # No ``expected_version``: every entry was checked at once above.
                instance.save()
                written[index] = instance.pk

            _set_pair_check("IMMEDIATE")
    except IntegrityError as exc:
        if SUPPLIER_PAIR_CONSTRAINT not in str(exc):
            raise
        # The end-state check above runs under the item's lock, so this is a
        # backstop for a writer that bypassed it; the transaction is gone.
        raise SupplierLinkBatchRefused(
            {index: {"non_field_errors": [PAIR_TAKEN_MESSAGE]} for index in range(len(changes))}
        ) from exc
    finally:
        # ``SET CONSTRAINTS`` lasts until the end of the TRANSACTION, not of this
        # block. Called inside a caller's transaction, a write that failed
        # part-way would otherwise leave the pair check deferred for whatever
        # that caller writes next.
        if connection.in_atomic_block and not connection.needs_rollback:
            _set_pair_check("IMMEDIATE")

    # Read back after the transaction: a promotion demotes rows in SQL, and each
    # save derives costs and versions the caller's copies do not show.
    fresh = ItemSupplier.objects.select_related("item", "supplier").in_bulk(written.values())
    return [fresh[written[index]] for index in range(len(changes))]


def _refuse_stale(changes: list[LinkChange], rows: dict[int, "ItemSupplier"]) -> None:
    from inventory.models.core import ItemSupplier

    for change in changes:
        if change.id is None or change.version is None:
            continue
        row = rows.get(change.id)
        if row is not None:
            if row.version != change.version:
                raise StaleSupplierLink(change.id, change.version, row.version)
        elif not ItemSupplier.objects.filter(pk=change.id).exists():
            # Deleted since it was loaded: the same refusal a versioned PATCH gets.
            raise StaleSupplierLink(change.id, change.version, None)


def _refuse_unwritable(changes: list[LinkChange], rows: dict[int, "ItemSupplier"]) -> None:
    from inventory.models.core import ItemSupplier

    errors: dict[int, dict[str, list[str]]] = {}

    def refuse(index: int, name: str, message: str) -> None:
        errors.setdefault(index, {}).setdefault(name, []).append(message)

    seen_ids: set[int] = set()
    promoted = [index for index, change in enumerate(changes) if change.fields.get("is_primary")]
    for index, change in enumerate(changes):
        if change.id is None:
            continue
        if change.id in seen_ids:
            refuse(index, "id", "This supplier link is named more than once in the request.")
        seen_ids.add(change.id)
        if change.id not in rows:
            refuse(
                index,
                "id",
                (
                    "This supplier link belongs to a different item."
                    if ItemSupplier.objects.filter(pk=change.id).exists()
                    else "No supplier link with this id exists."
                ),
            )
    if len(promoted) > 1:
        for index in promoted:
            refuse(index, "is_primary", "Only one supplier link in a request can be made primary.")

    if not errors:
        # Who holds each supplier once every entry has been applied.
        named = {change.id for change in changes if change.id is not None}
        holders: dict[Any, list[Optional[int]]] = {}
        for row in rows.values():
            if row.pk not in named:
                holders.setdefault(row.supplier_id, []).append(None)
        for index, change in enumerate(changes):
            row = rows.get(change.id) if change.id is not None else None
            holders.setdefault(_supplier_pk(change, row), []).append(index)
        for holding in holders.values():
            if len(holding) > 1:
                for index in holding:
                    if index is not None:
                        refuse(index, "non_field_errors", PAIR_TAKEN_MESSAGE)

    if errors:
        raise SupplierLinkBatchRefused(errors)
