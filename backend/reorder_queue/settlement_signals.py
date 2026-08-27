"""Re-derive a purchase order's status whenever one of its LINES moves.

Closing this door-by-door did not work. Each round found another way into the
admin — the change form, then the inline formset, then row delete, then the
bulk delete action, then reparenting a line onto a different order — and each
fix was another method name added to a hand-maintained list, which is the exact
shape of mistake this whole change exists to end. The obligation does not
belong to ``ModelAdmin.save_model``. It belongs to the LINE: if a line was
written or removed, whatever wrote it, the order it belongs to has to be asked
again what its status is.

So the routing lives here, on the model's own save/delete signals, and the
admin hooks that used to carry it are gone.

WHAT THIS DOES **NOT** COVER, and the reason the guard in
:mod:`reorder_queue.settlement_sites` still has a job:

    QUERYSETS DO NOT FIRE per-object save signals.

``PurchaseOrderItem.objects.filter(...).update(...)`` and ``bulk_update`` write
settlement columns straight to the database and this module never hears about
it. ``reorder_queue.services.purchase_orders.void_po`` is exactly such a path —
it strikes every line off with one ``update()`` — which is why it calls
``refresh_receipt_status`` explicitly and must keep doing so.

Nor does it cover a FAST DELETE. ``queryset.delete()`` normally fans out
``post_delete`` per row, and having a listener here is itself what forces that —
``Collector.can_fast_delete`` returns False for a model with delete-signal
listeners. But a collector that CAN fast-delete a set of rows issues one
``_raw_delete`` and sends no signal at all, and ``_raw_delete`` called directly
never does. That is a real hole, not a footnote, and the price of closing the
ordinary case is that deleting a purchase order now materialises its lines
instead of removing them in one statement.

Read those two as the boundary. A narrowing described as completeness is the
failure this file is the fourth attempt at closing.

Nor does every save re-derive. A save that moved no settlement field and did not
move the line to another order changes no answer, so it asks no question —
:func:`_remember_what_this_save_moves`. Without that, editing a line's note
would rewrite the order's status and bump its ``updated_at``, which is the same
silent overwrite the admin formset hook had one layer up.

Three properties this has to hold, all of them tested rather than asserted:

* **Coalesced.** Receiving twenty lines re-derives the order once, not twenty
  times — see :func:`settlement_batch`.
* **Same-request.** The re-derivation happens INSIDE the unit of work, never on
  ``transaction.on_commit``. Endpoints serialize ``purchase_order.status`` into
  the response they return after receiving, and ScanTTY reads it; deferring
  past the commit would answer with a stale status.
* **Non-re-entrant.** :data:`_state` carries an explicit flag so a refresh that
  ever comes to touch a line cannot re-enter its own signal. Today
  ``refresh_receipt_status`` writes only ``PurchaseOrder`` and so could not
  recurse anyway — that is a fact about today's code, not a guarantee, and it
  is not what this relies on.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from . import models
from .models import PurchaseOrder, PurchaseOrderItem

#: Per-thread routing state. ``pending`` is ``None`` outside a batch (immediate
#: mode) and a set of order ids inside one; ``refreshing`` is the re-entrancy
#: flag.
_state = threading.local()

#: The line's FK to its order, named once so the dirty check and the reparent
#: check read the same column.
_PARENT_FIELD = "purchase_order"

_settlement_fields_cache: frozenset[str] | None = None


def settlement_fields() -> frozenset[str]:
    """The columns that decide settlement, read off the model's own definition.

    Derived, never typed out here. :func:`reorder_queue.settlement_sites.derive_anchor`
    walks ``PurchaseOrderItem.is_settled`` transitively to the concrete fields,
    which is the same closure the guard enforces the rest of the tree against —
    so a field added to the definition joins the dirty check on its own. A tuple
    written into this module would be a hand-maintained FIELD list one layer
    below the hand-maintained METHOD list this module exists to delete.

    Read once, lazily: the walk parses ``models.py``, and doing that at import
    time would put a file read in every process start for a question only a line
    save asks.
    """
    global _settlement_fields_cache
    if _settlement_fields_cache is None:
        from .settlement_sites import derive_anchor

        models_path = Path(models.__file__)
        _settlement_fields_cache = derive_anchor(models_path, models_path.name).all_fields
    return _settlement_fields_cache


def _refreshing() -> bool:
    return getattr(_state, "refreshing", False)


def _run(order_ids) -> None:
    """Re-derive each order once, off a fresh read, with the signal suppressed.

    The read is deliberately not the caller's instance: a viewset that
    prefetched ``items`` holds a cached relation the line write did not
    invalidate, and deriving settlement from that cache is how an order
    finishes receiving and stays displayed as partially received.
    """
    from .services.receiving import refresh_receipt_status

    _state.refreshing = True
    try:
        for purchase_order in PurchaseOrder.objects.filter(pk__in=sorted(order_ids)):
            refresh_receipt_status(purchase_order)
    finally:
        _state.refreshing = False


def _mark(order_ids) -> None:
    order_ids = {order_id for order_id in order_ids if order_id is not None}
    if not order_ids or _refreshing():
        return
    pending = getattr(_state, "pending", None)
    if pending is None:
        _run(order_ids)
    else:
        pending.update(order_ids)


@contextmanager
def settlement_batch():
    """Hold the line writes in this block to ONE re-derivation per order.

    Correctness does not depend on using this: outside a batch every line write
    re-derives immediately, which is the same answer more times. What it buys
    is that receiving a twenty-line order asks the question once per order
    instead of once per line.

    Coalescing only — it opens no transaction of its own, so wrapping a block
    cannot quietly change whether that block is atomic. Callers that need
    atomicity keep saying so; the flush then runs inside whatever transaction
    they opened, and a rollback takes the re-derivation with it.

    The flush runs before the block returns, never on
    ``transaction.on_commit``: endpoints serialize the order's status into the
    response they return after receiving, and ScanTTY reads it.
    """
    outermost = getattr(_state, "pending", None) is None
    if outermost:
        _state.pending = set()
    try:
        yield
        if outermost:
            pending, _state.pending = _state.pending, set()
            _run(pending)
    finally:
        if outermost:
            _state.pending = None


@receiver(pre_save, sender=PurchaseOrderItem)
def _remember_what_this_save_moves(sender, instance, update_fields=None, **kwargs):
    """Decide, BEFORE the write, whether this save changes any answer.

    Two things have to be known and both are invisible afterwards:

    * whether a settlement field actually MOVED. A save that only rewrites a
      note, a landed cost or a shipment date settles nothing, and re-deriving
      the order over it would rewrite a status an operator may have chosen by
      hand and bump the order's ``updated_at`` where previously nothing touched
      the order at all.
    * which order the line is LEAVING. Reparenting is a settlement transition
      for two orders — the one that gains the line and the one left owed less
      than it was — and only the second cannot be read back after the save.

    Costs one narrow lookup per save of an existing line, skipped entirely when
    ``update_fields`` names nothing that matters. There is no cheaper honest
    answer: the values Django is about to write are on the instance, and the
    ones it is about to overwrite are only in the database.
    """
    instance._settlement_source_order_id = None
    instance._settlement_moved = True

    columns = sorted(settlement_fields())
    if update_fields is not None and not set(update_fields) & (
        set(columns) | {_PARENT_FIELD, f"{_PARENT_FIELD}_id"}
    ):
        instance._settlement_moved = False
        return
    if not instance.pk:
        return

    previous = (
        PurchaseOrderItem.objects.filter(pk=instance.pk)
        .values_list(f"{_PARENT_FIELD}_id", *columns)
        .first()
    )
    if previous is None:
        return

    source_order_id, previous_values = previous[0], previous[1:]
    instance._settlement_source_order_id = source_order_id
    instance._settlement_moved = source_order_id != instance.purchase_order_id or any(
        getattr(instance, column) != value for column, value in zip(columns, previous_values)
    )


@receiver(post_save, sender=PurchaseOrderItem)
def _rederive_after_line_save(sender, instance, **kwargs):
    if not getattr(instance, "_settlement_moved", True):
        return
    _mark({instance.purchase_order_id, getattr(instance, "_settlement_source_order_id", None)})


@receiver(post_delete, sender=PurchaseOrderItem)
def _rederive_after_line_delete(sender, instance, **kwargs):
    """A delete writes no settlement field and still changes the answer.

    Fires for ``queryset.delete()`` too, which is what closes the admin's bulk
    "Delete selected" action without the admin knowing anything about it.

    When a purchase order is deleted, its lines go FIRST — ``Collector`` deletes
    a dependent model before the model it points at, and sends ``post_delete``
    per row straight after that model's batch — so this fires while the order
    row is still there and :func:`_run` duly re-reads it. What makes that
    harmless is not that the order is gone: it is that every line already is, so
    ``has_received_anything`` is False and the refresh returns without writing.

    A collector that can FAST-delete sends no signal at all, and neither does
    ``_raw_delete``; see this module's own boundary above.
    """
    _mark({instance.purchase_order_id})
