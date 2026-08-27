"""Re-derive a purchase order whenever one of its LINES moves.

Closing this door-by-door did not work. Each round found another way into the
admin — the change form, then the inline formset, then row delete, then the
bulk delete action, then reparenting a line onto a different order — and each
fix was another method name added to a hand-maintained list, which is the exact
shape of mistake this whole change exists to end. The obligation does not
belong to ``ModelAdmin.save_model``. It belongs to the LINE: if a line was
written or removed, whatever wrote it, the order it belongs to has to be asked
its questions again.

Two questions, not one, and they do not have the same triggers. Every line
SAVE that moved something can change the order's settlement status. The
order's stored ``estimated_total`` moves on the narrower rule "a line's cost
LEFT the order", which has two routes rather than one: a DELETE, and a SAVE
that REPARENTS the line onto a different order — the second is a removal from
the order it left and an addition to the order it joined, so both owe a
re-roll. See :func:`_rederive_after_line_delete` for what the stored total is
and why nobody else subtracts a line that is gone, and
:func:`_rederive_after_line_save` for why ordinary saves are still excluded.

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


def _run(order_ids, cost_order_ids=()) -> None:
    """Re-derive each order once, off a fresh read, with the signal suppressed.

    The read is deliberately not the caller's instance: a viewset that
    prefetched ``items`` holds a cached relation the line write did not
    invalidate, and deriving settlement from that cache is how an order
    finishes receiving and stays displayed as partially received.

    ``cost_order_ids`` is the subset owing a stored-total re-roll as well —
    the orders a line's cost LEFT or JOINED, which is deletes plus reparents;
    see :func:`_rederive_after_line_delete` for why.

    ONE read covers both questions, and deliberately so. Reading the orders
    twice — once for the money, once for the status — would still be coalesced
    per unit of work rather than per row, so nothing would look wrong, and the
    test that counts re-derivations caught it precisely because it counts the
    READ rather than the write. An order re-derived per unit of work should be
    fetched once per unit of work.

    Money first on each instance, so the one the status derivation then reads
    is already whole.
    """
    from .services.purchase_orders import recalculate_estimated_total
    from .services.receiving import refresh_receipt_status

    order_ids = set(order_ids)
    cost_order_ids = set(cost_order_ids)
    _state.refreshing = True
    try:
        for purchase_order in PurchaseOrder.objects.filter(
            pk__in=sorted(order_ids | cost_order_ids)
        ):
            if purchase_order.pk in cost_order_ids:
                recalculate_estimated_total(purchase_order)
            if purchase_order.pk in order_ids:
                refresh_receipt_status(purchase_order)
    finally:
        _state.refreshing = False


def _mark(order_ids, *, costs: bool = False) -> None:
    order_ids = {order_id for order_id in order_ids if order_id is not None}
    if not order_ids or _refreshing():
        return
    pending = getattr(_state, "pending", None)
    if pending is None:
        _run(order_ids, order_ids if costs else ())
    else:
        pending.update(order_ids)
        if costs:
            _state.pending_costs.update(order_ids)


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
        _state.pending_costs = set()
    try:
        yield
        if outermost:
            pending, _state.pending = _state.pending, set()
            pending_costs, _state.pending_costs = _state.pending_costs, set()
            _run(pending, pending_costs)
    finally:
        if outermost:
            _state.pending = None
            _state.pending_costs = None


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
    """Re-derive the order this save touched, and the one it may have left.

    Settlement status for both. The stored ``estimated_total`` only when the
    line was REPARENTED, because that is the one save that removes a cost from
    an order: the source order is left carrying money for a line it no longer
    holds, exactly as a DELETE would leave it, and the destination is left
    understating by the same amount. The rule
    :func:`_rederive_after_line_delete` states — "a line's cost left the order,
    and it applies to every route that can remove one" — is why this branch is
    here rather than in the admin form that happens to be today's only reparent
    door.

    Ordinary saves are still excluded, and the reparent branch does not weaken
    that: a save that merely moved a settlement field already re-rolls the
    total on its own path (``add_line_item``, ``update_item``), so re-rolling
    from here as well would write the order — and bump its ``updated_at`` — on
    every line save, which is what :func:`_remember_what_this_save_moves`
    exists to prevent one layer up. A reparent is distinguished before the
    write and asks the question only for the two orders that really moved.
    """
    if not getattr(instance, "_settlement_moved", True):
        return
    source_order_id = getattr(instance, "_settlement_source_order_id", None)
    reparented = source_order_id is not None and source_order_id != instance.purchase_order_id
    _mark({instance.purchase_order_id, source_order_id}, costs=reparented)


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

    ``costs=True`` because a DELETE breaks a second invariant, and it rides the
    same signal rather than growing a second receiver to keep in step with this
    one.

    ``PurchaseOrder.estimated_total`` is STORED: frozen at create time from the
    sum of the line costs and re-rolled by ``recalculate_estimated_total`` at
    every site that moves one. Voiding is deliberately not such a site — a
    voided line stays in the stored sum and ``effective_estimated_total``
    subtracts it at read time, which is what keeps the struck-off money
    visible. A DELETED line is subtracted by nobody: it is gone from ``items``,
    so the read-time subtraction cannot see it, while the stored sum it was
    added to still carries its cost. The order then reports — on its detail
    page, in ``payment_schedule``, and to every API client — money for a line
    that does not exist, and no operator action brings the two back into line.

    So the rule is "a line's cost left the order", and it applies to every
    route that can remove one. That is why it is here and not in the delete
    endpoint: the endpoint is one such route, and the admin's row delete,
    inline delete and bulk "Delete selected" are three more that were reachable
    — and already overstating the total — before that endpoint existed. Fixing
    only the new door would have left the older three wrong and called it done.

    A DELETE is not the only such route, and stating the rule without honouring
    it is how the fifth door stays open: the admin's change form can also MOVE a
    line to another order, which removes its cost from the order it left just as
    finally. That route re-rolls from :func:`_rederive_after_line_save`, on the
    same rule and for the same reason.

    Ordinary saves are untouched — see :func:`_rederive_after_line_save` for
    what separates them from a reparent.

    An order-level figure computed from lines that only SOME line-writing paths
    re-derive is the general shape filed as ``oms-derived-totals-beyond-
    settlement``; the reparent gap above is a worked instance of it, down to
    how it was found (the rule was written down, then read back against every
    route that can satisfy its antecedent).
    """
    _mark({instance.purchase_order_id}, costs=True)
