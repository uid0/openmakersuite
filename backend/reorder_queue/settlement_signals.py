"""Re-derive a purchase order whenever one of its LINES moves.

Closing this door-by-door did not work. Each round found another way into the
admin — the change form, then the inline formset, then row delete, then the
bulk delete action, then reparenting a line onto a different order — and each
fix was another method name added to a hand-maintained list, which is the exact
shape of mistake this whole change exists to end. The obligation does not
belong to ``ModelAdmin.save_model``. It belongs to the LINE: if a line was
written or removed, whatever wrote it, the order it belongs to has to be asked
its questions again.

ONE RULE, ONCE, FOR EVERY ORDER-LEVEL VALUE COMPUTED FROM THE LINES
-------------------------------------------------------------------

There is more than one such value, and this module used to know that as a
special case rather than as a rule: the settlement status re-derived on any
line save that moved a settlement field, while the stored ``estimated_total``
re-derived on the narrower "a line's cost LEFT the order" (delete, or a
reparent). A line whose PRICE was edited moved the money and re-derived
nothing, which is how an admin reprice left an order reporting a total its own
lines had stopped adding up to.

So the values are DECLARED, in :data:`DERIVED_ORDER_VALUES`, and the rule is
stated once for all of them:

    A line SAVE re-derives value V for the affected order(s) exactly when one
    of V's own INPUT fields moved, or when the line was created, or when it was
    reparented onto a different order. A line DELETE re-derives every value for
    the order it left.

Nothing here lists which fields those are. Each value names the member of
:class:`~reorder_queue.models.PurchaseOrderItem` its derivation is read off —
its SEED — and :func:`line_inputs` follows that seed through the imported class
until only concrete columns are left. Add a field to a derivation and it joins
that value's dirty check on its own; give the model a new order-level total and
it is one entry here, not a new branch.

The set is not taken on trust either.
:mod:`reorder_queue.settlement_sites` derives, from the tree, every stored
``PurchaseOrder`` column that anything computes from the lines, and FAILS THE
BUILD when one of them is not declared here — so the next
``estimated_total`` cannot be added quietly and discovered later.

So the routing lives here, on the model's own save/delete signals, and the
admin hooks that used to carry it are gone.

WHAT THIS DOES **NOT** COVER, and the reason the guard in
:mod:`reorder_queue.settlement_sites` still has a job:

    QUERYSETS DO NOT FIRE per-object save signals.

``PurchaseOrderItem.objects.filter(...).update(...)`` and ``bulk_update`` write
settlement AND cost columns straight to the database and this module never
hears about it. ``reorder_queue.services.purchase_orders.void_po`` is exactly
such a path — it strikes every line off with one ``update()`` — which is why it
calls ``refresh_receipt_status`` explicitly and must keep doing so. The guard
holds every value's writers to that same requirement, not just settlement's.

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

Nor does every save re-derive every value, or any value. A save that moved none
of a value's inputs and did not move the line to another order changes that
value's answer not at all, so it asks nothing —
:func:`_remember_what_this_save_moves`. Without that, editing a line's note
would rewrite the order's status and bump its ``updated_at``, which is the same
silent overwrite the admin formset hook had one layer up. The gate is per
VALUE, which is what keeps receiving out of the money: a receipt moves
``quantity_received``, which is a settlement input and not a cost one, so the
status re-derives and the stored total is not touched.

Three properties this has to hold, all of them tested rather than asserted:

* **Coalesced.** Receiving twenty lines re-derives the order once, not twenty
  times — see :func:`settlement_batch`. Per value: an order owing only a
  status re-derivation is not re-priced to keep it company.
* **Same-request.** The re-derivation happens INSIDE the unit of work, never on
  ``transaction.on_commit``. Endpoints serialize ``purchase_order.status`` into
  the response they return after receiving, and ScanTTY reads it; deferring
  past the commit would answer with a stale status.
* **Non-re-entrant.** :data:`_state` carries an explicit flag so a refresh that
  ever comes to touch a line cannot re-enter its own signal. Today no
  re-derivation writes anything but ``PurchaseOrder`` and so none could recurse
  anyway — that is a fact about today's code, not a guarantee, and it is not
  what this relies on.
"""

from __future__ import annotations

import functools
import importlib
import inspect
import threading
import types
from contextlib import contextmanager
from dataclasses import dataclass

from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .models import PurchaseOrder, PurchaseOrderItem

#: Per-thread routing state. ``pending`` is ``None`` outside a batch (immediate
#: mode) and, inside one, a ``{value name: set of order ids}`` map;
#: ``refreshing`` is the re-entrancy flag.
_state = threading.local()

#: The line's FK to its order, named once so the dirty check and the reparent
#: check read the same column.
_PARENT_FIELD = "purchase_order"


@dataclass(frozen=True)
class DerivedOrderValue:
    """One value STORED on a purchase order and COMPUTED from that order's lines.

    Three facts, and only these three, because everything else about the value
    is derivable from them:

    * :attr:`column` — the ``PurchaseOrder`` field it is stored in. The guard
      matches this against the columns it finds being computed from the lines
      out in the tree, which is how a value nobody declared here fails the
      build instead of waiting to be discovered.
    * :attr:`seed` — the member of ``PurchaseOrderItem`` the value's derivation
      is read OFF each line. :func:`line_inputs` walks it to the concrete
      columns, so the input set is never typed out.
    * :attr:`module` / :attr:`refresh` — the function that re-derives and
      persists it. Named rather than passed as a callable so importing this
      module does not drag the service layer in, and so
      :mod:`reorder_queue.settlement_sites` can require a call to it BY NAME:
      a re-implementation under a different name discharges nothing.
    """

    column: str
    seed: str
    module: str
    refresh: str

    @property
    def inputs(self) -> frozenset[str]:
        """The line columns this value is computed from."""
        return line_inputs(self.seed)

    def rederive(self, purchase_order) -> None:
        """Recompute and persist this value on ``purchase_order``."""
        module = importlib.import_module(self.module, package=__package__)
        getattr(module, self.refresh)(purchase_order)


#: Every order-level value computed from the lines, in the order they are
#: re-derived. Money BEFORE status, so the instance the status derivation then
#: reads is already whole.
#:
#: Two entries, and that is not an assertion: ``settlement_sites`` derives the
#: same set from the tree — every stored ``PurchaseOrder`` column that anything
#: computes from the lines — and fails the build if it finds one that is not
#: here. Read that guard, not this tuple, as the claim of completeness.
DERIVED_ORDER_VALUES = (
    DerivedOrderValue(
        column="estimated_total",
        seed="estimated_cost",
        module=".services.purchase_orders",
        refresh="recalculate_estimated_total",
    ),
    DerivedOrderValue(
        column="status",
        seed="is_settled",
        module=".services.receiving",
        refresh="refresh_receipt_status",
    ),
)


def _invalidate_parent_snapshot(instance: PurchaseOrderItem) -> None:
    parent = instance._state.fields_cache.get(_PARENT_FIELD)
    if parent is None:
        return
    parent.__dict__.pop("_line_item_totals", None)
    getattr(parent, "_prefetched_objects_cache", {}).pop("items", None)


def _member_code(name: str) -> types.CodeType | None:
    """The compiled body of a class member, or ``None`` if it has no body.

    ``getattr_static`` deliberately, not ``getattr``: reading a property off
    the CLASS would evaluate nothing useful, and reading a descriptor could run
    code. This wants the object as declared.
    """
    attr = inspect.getattr_static(PurchaseOrderItem, name, None)
    if isinstance(attr, property):
        attr = attr.fget
    elif isinstance(attr, functools.cached_property):
        attr = attr.func
    elif isinstance(attr, (classmethod, staticmethod)):
        attr = attr.__func__
    return attr.__code__ if isinstance(attr, types.FunctionType) else None


def _names_touched(code: types.CodeType) -> set[str]:
    """Every name a compiled body refers to, comprehensions and lambdas included."""
    names = set(code.co_names)
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            names |= _names_touched(const)
    return names


@functools.cache
def line_inputs(seed: str) -> frozenset[str]:
    """The line columns ``seed``'s derivation reads, from the model's own definition.

    Derived, never typed out here, and derived from the IMPORTED CLASS rather
    than from source. Start at ``seed``, follow the names each member's
    compiled body touches, and stop at the ones that are concrete fields: that
    closure IS the derivation, so a field added to one joins the dirty check on
    its own. A tuple written into this module would be a hand-maintained FIELD
    list one layer below the hand-maintained METHOD list this module exists to
    delete — and it would need one such list PER VALUE, which is the same
    mistake again with a multiplier on it.

    :func:`reorder_queue.settlement_sites.derive_anchor` computes the SAME
    closure by parsing ``models.py``, and that is where it belongs — it is a
    static guard, run by CI over a checkout. It has no business on the ORM
    write path, which is where this used to call it: a ``pre_save`` receiver
    that reads and AST-parses a source file makes every line save depend on
    ``models.py`` being present, readable and parseable by the running
    interpreter, and turns any of those failing into a failed database write
    rather than a failed build. Nothing on this path needs the source; the
    class is already imported.

    The two derivations are independent and must agree, for every value — that
    is asserted in ``reorder_queue/tests/test_settlement_sites.py``, so neither
    can drift from a definition without the build saying so.
    """
    columns = {f.name for f in PurchaseOrderItem._meta.concrete_fields}
    found: set[str] = set()
    seen: set[str] = set()
    pending = [seed]
    while pending:
        member = pending.pop()
        if member in seen:
            continue
        seen.add(member)
        code = _member_code(member)
        if code is None:
            continue
        for name in _names_touched(code):
            if name in columns:
                found.add(name)
            elif name not in seen:
                pending.append(name)
    return frozenset(found)


@functools.cache
def _watched_columns() -> tuple[str, ...]:
    """Every line column any declared value is computed from, sorted, once.

    The union, because ``pre_save`` reads the pre-write row ONCE and then asks
    each value whether ITS share of that row moved. Reading per value would be
    one narrow query per value on every line save to answer a question one
    query already answers.
    """
    watched: set[str] = set()
    for value in DERIVED_ORDER_VALUES:
        watched |= value.inputs
    return tuple(sorted(watched))


def _refreshing() -> bool:
    return getattr(_state, "refreshing", False)


def _run(pending) -> None:
    """Re-derive each order once, off a fresh read, with the signal suppressed.

    ``pending`` is ``{value name: set of order ids}`` — which orders owe which
    of :data:`DERIVED_ORDER_VALUES`. An order can owe one, the other, or both,
    and asking it only what actually moved is what keeps a receipt from
    rewriting money and a note edit from rewriting anything.

    The read is deliberately not the caller's instance: a viewset that
    prefetched ``items`` holds a cached relation the line write did not
    invalidate, and deriving settlement from that cache is how an order
    finishes receiving and stays displayed as partially received.

    ONE read covers every value, and deliberately so. Reading the orders once
    per value would still be coalesced per unit of work rather than per row, so
    nothing would look wrong, and the test that counts re-derivations caught it
    precisely because it counts the READ rather than the write. An order
    re-derived per unit of work should be fetched once per unit of work.

    Values are re-derived in :data:`DERIVED_ORDER_VALUES` order — money first,
    so the instance the status derivation then reads is already whole.
    """
    owed = {value.column: set(pending.get(value.column, ())) for value in DERIVED_ORDER_VALUES}
    every_order = set().union(*owed.values()) if owed else set()
    if not every_order:
        return
    _state.refreshing = True
    try:
        with transaction.atomic():
            purchase_orders = PurchaseOrder.objects.select_for_update().filter(
                pk__in=sorted(every_order)
            )
            for purchase_order in purchase_orders.order_by("pk"):
                for value in DERIVED_ORDER_VALUES:
                    if purchase_order.pk in owed[value.column]:
                        value.rederive(purchase_order)
    finally:
        _state.refreshing = False


def _mark(order_ids, values) -> None:
    """Record that ``order_ids`` owe ``values``, running now or at batch flush.

    ``values`` is the iterable of :class:`DerivedOrderValue` the caller has
    established really moved. Passing the whole tuple is what a DELETE does —
    a line leaving takes every value it fed with it — and a SAVE passes only
    the ones whose own inputs moved.
    """
    order_ids = {order_id for order_id in order_ids if order_id is not None}
    values = tuple(values)
    if not order_ids or not values or _refreshing():
        return
    owed = {value.column: set(order_ids) for value in values}
    pending = getattr(_state, "pending", None)
    if pending is None:
        _run(owed)
    else:
        for column, ids in owed.items():
            pending.setdefault(column, set()).update(ids)


@contextmanager
def settlement_batch():
    """Hold the line writes in this block to ONE re-derivation per order per value.

    Correctness does not depend on using this: outside a batch every line write
    re-derives immediately, which is the same answer more times. What it buys
    is that receiving a twenty-line order asks the question once per order
    instead of once per line.

    Named for settlement because settlement is what it was built for, and kept
    under that name because it is called from the admin and from four services;
    it coalesces every value in :data:`DERIVED_ORDER_VALUES`, and each one
    separately, so an order that owes only a status re-derivation is not
    re-priced to keep it company.

    Coalescing only — it opens no transaction of its own, so wrapping a block
    cannot quietly change whether that block is atomic. Callers that need
    atomicity keep saying so; the flush then runs inside whatever transaction
    they opened, and a rollback takes the re-derivation with it.

    An exceptional exit drops the queued re-derivations. Every caller is
    atomic, so the writes that queued them roll back too; a future non-atomic
    caller would have to account for that boundary explicitly.

    The flush runs before the block returns, never on
    ``transaction.on_commit``: endpoints serialize the order's status into the
    response they return after receiving, and ScanTTY reads it.
    """
    outermost = getattr(_state, "pending", None) is None
    if outermost:
        _state.pending = {}
    try:
        yield
        if outermost:
            pending, _state.pending = _state.pending, {}
            _run(pending)
    finally:
        if outermost:
            _state.pending = None


@receiver(pre_save, sender=PurchaseOrderItem)
def _remember_what_this_save_moves(sender, instance, update_fields=None, **kwargs):
    """Decide, BEFORE the write, which values this save can have changed.

    Two things have to be known and both are invisible afterwards:

    * which of :data:`DERIVED_ORDER_VALUES` had one of ITS OWN inputs move. A
      save that only rewrites a note, a landed cost or a shipment date moves
      none of them and re-derives nothing, which is what stops it rewriting a
      status an operator chose by hand and bumping the order's ``updated_at``
      where previously nothing touched the order at all. A save that reprices a
      line moves the money and not the settlement; a receipt moves the
      settlement and not the money. Answering per value rather than with one
      flag is the whole of that.
    * which order the line is LEAVING. Reparenting moves EVERY value for two
      orders — the one that gains the line and the one left owed less than it
      was — and only the second cannot be read back after the save.

    A line that does not exist yet moves everything: there is no previous row
    to compare against, and a line joining an order changes every value that
    order computes from its lines.

    Costs one narrow lookup per save of an existing line, skipped entirely when
    ``update_fields`` names nothing that matters. There is no cheaper honest
    answer: the values Django is about to write are on the instance, and the
    ones it is about to overwrite are only in the database.
    """
    instance._derived_source_order_id = None
    instance._derived_moved = DERIVED_ORDER_VALUES

    columns = _watched_columns()
    if update_fields is not None and not set(update_fields) & (
        set(columns) | {_PARENT_FIELD, f"{_PARENT_FIELD}_id"}
    ):
        instance._derived_moved = ()
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
    instance._derived_source_order_id = source_order_id
    if source_order_id != instance.purchase_order_id:
        # A reparent is a removal from one order and an addition to the other.
        # Every value both orders compute from their lines moved.
        return

    was = dict(zip(columns, previous_values))
    instance._derived_moved = tuple(
        value
        for value in DERIVED_ORDER_VALUES
        if any(getattr(instance, column) != was[column] for column in value.inputs)
    )


@receiver(post_save, sender=PurchaseOrderItem)
def _rederive_after_line_save(sender, instance, **kwargs):
    """Re-derive what this save moved, on the order it touched and the one it left.

    Which values those are was decided before the write, per value, by
    :func:`_remember_what_this_save_moves` — this receiver only routes the
    answer, because the two orders a reparent concerns are the only part of it
    still readable here.

    What that leaves covered, and what it does not:

    * COVERED — a line's inputs to a value MOVING, by any route that fires a
      per-object save. A quantity edit moves both the settlement and the money;
      a reprice moves the money alone; a receipt moves the settlement alone. It
      does not matter whether the write came from the API, the Django admin's
      change form, its inline formset, a management command or a shell.
    * COVERED — a line's cost LEAVING an order, by any route. Delete
      (:func:`_rederive_after_line_delete`) and reparent (here), which is the
      pair the rule "a line's cost left the order" already named.
    * NOT COVERED — a write that fires no per-object save signal:
      ``queryset.update()``, ``bulk_update``, ``bulk_create``, a fast delete,
      raw SQL. That is this module's standing boundary, stated at the top, and
      it is why :mod:`reorder_queue.settlement_sites` still holds every such
      writer to a NAMED call to the value's own re-derivation.

    The narrow gate is what makes the coverage above affordable. Re-rolling
    every value on every line save would write the order — and bump its
    ``updated_at`` — whenever anyone edited a note, and would rewrite money on
    every receipt.
    """
    _invalidate_parent_snapshot(instance)
    moved = getattr(instance, "_derived_moved", DERIVED_ORDER_VALUES)
    if not moved:
        return
    source_order_id = getattr(instance, "_derived_source_order_id", None)
    _mark({instance.purchase_order_id, source_order_id}, moved)


@receiver(post_delete, sender=PurchaseOrderItem)
def _rederive_after_line_delete(sender, instance, **kwargs):
    """A delete writes no field at all and still changes every answer.

    Fires for ``queryset.delete()`` too, which is what closes the admin's bulk
    "Delete selected" action without the admin knowing anything about it.

    When a purchase order is deleted, its lines go FIRST — ``Collector`` deletes
    a dependent model before the model it points at, and sends ``post_delete``
    per row straight after that model's batch — so this fires while the order
    row is still there and :func:`_run` duly re-reads it. What makes that
    harmless is not that the order is gone: it is that every line already is, so
    ``has_received_anything`` is False and the status refresh returns without
    writing, and the total is re-rolled to the zero its lines now sum to.

    A collector that can FAST-delete sends no signal at all, and neither does
    ``_raw_delete``; see this module's own boundary above.

    EVERY value, because a line that is gone fed all of them and the fields it
    fed them with are gone with it. There is nothing to compare, so there is
    nothing to narrow by, and narrowing here on the strength of what the
    instance still holds in memory would be guessing.

    The money case is the one that had no other owner.
    ``PurchaseOrder.estimated_total`` is STORED: frozen at create time from the
    sum of the line costs and re-rolled by ``recalculate_estimated_total`` at
    every site that moves one. Voiding is deliberately not such a site — a
    voided line stays in the stored sum and ``effective_estimated_total``
    subtracts it at read time, which is what keeps the struck-off money
    visible; ``is_voided`` is a settlement input and not a cost one, so the
    per-value gate keeps it that way without a special case. A DELETED line is
    subtracted by nobody: it is gone from ``items``, so the read-time
    subtraction cannot see it, while the stored sum it was added to still
    carries its cost. The order then reports — on its detail page, in
    ``payment_schedule``, and to every API client — money for a line that does
    not exist, and no operator action brings the two back into line.

    So the rule is "a line's cost left the order", and it applies to every
    route that can remove one. That is why it is here and not in the delete
    endpoint: the endpoint is one such route, and the admin's row delete,
    inline delete and bulk "Delete selected" are three more that were reachable
    — and already overstating the total — before that endpoint existed. Fixing
    only the new door would have left the older three wrong and called it done.

    A DELETE is not the only such route, and stating the rule without honouring
    it is how the fifth door stays open: the admin's change form can also MOVE a
    line to another order, which removes its cost from the order it left just as
    finally, and can REPRICE one, which changes the cost without moving it
    anywhere. Both re-roll from :func:`_rederive_after_line_save`, on the same
    rule and for the same reason.
    """
    _invalidate_parent_snapshot(instance)
    _mark({instance.purchase_order_id}, DERIVED_ORDER_VALUES)
