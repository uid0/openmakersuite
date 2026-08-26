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
``refresh_receipt_status`` explicitly and must keep doing so. Deletes are
different: ``queryset.delete()`` does fan out ``post_delete`` per row, so bulk
deletion IS covered; only ``_raw_delete`` is not.

Read that as the boundary, not as a footnote. A narrowing described as
completeness is the failure this file is the fourth attempt at closing.

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

from django.db import transaction
from django.db.models.signals import post_delete, post_init, post_save, pre_save
from django.dispatch import receiver

from .models import PurchaseOrder, PurchaseOrderItem

#: Per-thread routing state. ``pending`` is ``None`` outside a batch (immediate
#: mode) and a set of order ids inside one; ``refreshing`` is the re-entrancy
#: flag.
_state = threading.local()

#: Where :func:`_remember_loaded_order` parks the parent a line was READ with,
#: so a reparent can name the order the line left as well as the one it joined.
_LOADED_ORDER = "_settlement_loaded_order_id"


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
    """One transaction whose line writes re-derive each order once at the end.

    Correctness does not depend on using this: outside a batch every line write
    re-derives immediately, which is the same answer more times. What it buys
    is that receiving a twenty-line order asks the question once per order
    instead of once per line.

    The flush runs INSIDE the transaction and before the block returns, so the
    caller's response sees the re-derived status and a rollback takes the
    re-derivation with it.
    """
    outermost = getattr(_state, "pending", None) is None
    if outermost:
        _state.pending = set()
    try:
        with transaction.atomic():
            yield
            if outermost:
                pending, _state.pending = _state.pending, set()
                _run(pending)
    finally:
        if outermost:
            _state.pending = None


@receiver(post_init, sender=PurchaseOrderItem)
def _remember_loaded_order(sender, instance, **kwargs):
    """Park the parent this line was LOADED with, free of charge.

    Reading it here rather than querying in ``pre_save`` keeps the common path
    query-free. ``_state.adding`` tells a row read from the database apart from
    one merely constructed with a primary key, and only the former knows its
    own source order; the latter falls back to a lookup in
    :func:`_remember_source_order`. A deferred column is absent from
    ``__dict__``, so this never triggers a load either.
    """
    instance.__dict__[_LOADED_ORDER] = (
        None if instance._state.adding else instance.__dict__.get("purchase_order_id")
    )


@receiver(pre_save, sender=PurchaseOrderItem)
def _remember_source_order(sender, instance, **kwargs):
    """The order this line is leaving, if it is moving.

    Reparenting a line is a settlement transition for TWO orders: the one that
    gains it and the one that is left owed less than it was. Only the second is
    invisible after the save, so it has to be captured before.
    """
    instance._settlement_source_order_id = None
    if not instance.pk:
        return
    loaded = instance.__dict__.get(_LOADED_ORDER)
    if loaded is None:
        loaded = (
            PurchaseOrderItem.objects.filter(pk=instance.pk)
            .values_list("purchase_order_id", flat=True)
            .first()
        )
    instance._settlement_source_order_id = loaded


@receiver(post_save, sender=PurchaseOrderItem)
def _rederive_after_line_save(sender, instance, **kwargs):
    _mark({instance.purchase_order_id, getattr(instance, "_settlement_source_order_id", None)})


@receiver(post_delete, sender=PurchaseOrderItem)
def _rederive_after_line_delete(sender, instance, **kwargs):
    """A delete writes no settlement field and still changes the answer.

    Fires for ``queryset.delete()`` too, which is what closes the admin's bulk
    "Delete selected" action without the admin knowing anything about it. An
    order deleted along with its own lines simply is not there to re-derive,
    and :func:`_run` reads the survivors rather than assuming.
    """
    _mark({instance.purchase_order_id})
