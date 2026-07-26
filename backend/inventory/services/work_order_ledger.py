"""Committee ledger charge for a completed work order (op-u2g5, Phase 2 · B6).

Finishing a job on a committee-owned machine spends the committee's money. This
module is the one seam that says so in the books: when a work order transitions
into ``completed`` it posts a **single aggregate** ``SIG_CHARGE`` for everything
the job actually consumed::

    DR 5100 Committee supplies expense   (dimensions: sig=committee, asset=asset)
    CR 1300 Inventory — Supplies on hand

It is the work-order half of the mapping ``log_usage`` has used since Phase 2 ·
Bead 1 — the path :func:`accounting.adapters.post_supply_consumption` was
explicitly reserved for — so the two consumption routes book identically and a
committee statement reads as one story.

Three decisions worth stating out loud
--------------------------------------

**The asset's committee is the cost centre; material lines are not filtered.**
``committee = work_order.asset.owning_group``. A job on a space-owned machine
posts nothing at all, and a job on a committee-owned one charges that committee
for *everything it consumed* — shop stock, the committee's own stock, and
out-of-pocket receipts alike. Filtering the basis down to lines whose inventory
item happens to carry the same ``owning_group`` would drop exactly the case
corrective work exists for: an ad-hoc, out-of-pocket line has no inventory item
to own (see :attr:`WorkOrderMaterialUsage.stock_item`), so it would silently
escape the charge.

**One entry per job, not one per material line.** The basis is
:attr:`WorkOrder.consumed_material_cost` — ``quantity_used × unit_cost`` summed
over the lines actually marked *used*. Lines with no recorded cost contribute
nothing rather than blocking the post, so a partially-priced job still charges
what is known. Vendor invoices are a separate, still-unwired flow
(``SourceType.VENDOR_INVOICE``): the basis here is **in-house consumption only**.

That is deliberately *not* the same number the work-order screen and the cost
reports show. Since op-4pzp those read :attr:`WorkOrder.actual_material_cost`,
which counts a priced **ad-hoc** line the moment it is entered — money spent on
the job, whether or not anyone ever marks it used. This entry credits ``1300
Inventory — supplies on hand``, an assertion that stock left the shelf, so it
stays keyed to consumption: charging it for a freehand line nobody drew from
inventory would write down stock that was never issued. Job cost ≥ ledger
charge, and the gap is exactly the out-of-pocket spend.

**Reopening reverses; re-completing re-posts.** A charge is keyed
``wo_complete:<id>``, which makes re-saving a completed work order a no-op
(the entry already exists). But a *reopened* job must not stay charged, so
:func:`reverse_work_order_charge` posts an append-only ``REVERSAL`` — and a
re-completion after that has to charge again, which the original key alone
could never do. So a repeat cycle keys ``wo_complete:<id>:2``, ``:3``, … and
the two entry points share one rule: **at most one un-reversed charge per work
order at any time**. Nothing is ever edited or deleted; the balance simply walks
back and forth.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover
    from django.contrib.auth.models import Group

    from hordak.models import Transaction

    from ..models import WorkOrder

logger = logging.getLogger(__name__)

#: Prefix of every completion charge's idempotency key. The work-order UUID that
#: follows is fixed-width, so ``startswith`` over the family cannot collide with
#: another work order's keys.
SOURCE_REF_PREFIX = "wo_complete:"

#: Returned by :func:`charge_completed_work_order` when a committee-owned job had
#: nothing to charge — the work-order analogue of ``log_usage``'s no-cost warning.
NO_COST_WARNING = (
    "committee-owned asset, but no material costs were recorded on this "
    "work order — nothing posted to the ledger"
)


def _recorded_actor(actor):
    """Only a real, logged-in user is recorded on an entry's provenance."""
    return actor if actor is not None and getattr(actor, "is_authenticated", False) else None


def _source_ref_base(work_order: "WorkOrder") -> str:
    return f"{SOURCE_REF_PREFIX}{work_order.id}"


def charge_committee(work_order: "WorkOrder") -> Optional["Group"]:
    """The committee a completed ``work_order`` bills to, or ``None``.

    The machine's owner pays: ``asset.owning_group`` via the :class:`OwnableModel`
    mixin. A space-owned or user-owned asset has no committee and never touches
    the ledger. Every work order carries an ``asset`` (``save()`` derives it from
    the PM template when one is given), but the FK is nullable in the schema, so
    read it defensively.
    """
    asset = work_order.asset if work_order.asset_id else None
    if asset is None:
        return None
    return asset.owning_group if asset.owning_group_id else None


def _charge_metas(work_order: "WorkOrder"):
    """Every completion-charge ``EntryMeta`` for this work order, oldest first.

    Annotated with ``is_reversed`` so the caller can find the live charge (and
    the next free key) without a query per row.
    """
    from django.db.models import Exists, OuterRef

    from accounting.models import EntryMeta, SourceType

    reversals = EntryMeta.objects.filter(reverses_id=OuterRef("transaction_id"))
    return (
        EntryMeta.objects.filter(
            source_type=SourceType.SIG_CHARGE,
            source_ref__startswith=_source_ref_base(work_order),
        )
        .annotate(is_reversed=Exists(reversals))
        .select_related("transaction")
        .order_by("posted_at", "id")
    )


def active_charge(work_order: "WorkOrder") -> Optional["Transaction"]:
    """The work order's live (posted, un-reversed) charge ``Transaction``, or ``None``."""
    for meta in _charge_metas(work_order):
        if not meta.is_reversed:
            return meta.transaction
    return None


def charge_completed_work_order(
    work_order: "WorkOrder", *, actor=None
) -> tuple[Optional["Transaction"], Optional[str]]:
    """Charge ``work_order``'s committee for what the job consumed.

    Returns ``(transaction, warning)``. ``transaction`` is ``None`` when nothing
    was posted; ``warning`` is set only in the case worth telling a human about —
    a committee-owned job that finished with no priced materials, mirroring
    ``log_usage``'s "committee recorded, but … nothing posted" response. A job on
    a space-owned asset is the shop's default and warns about nothing.

    Safe to call more than once: while a charge for this work order stands
    un-reversed it is returned unchanged rather than re-posted, so re-saving a
    completed work order never double-charges. After a reversal (see
    :func:`reverse_work_order_charge`) the next call posts a fresh entry under the
    next key in the family, which is what makes reopen → re-complete restore the
    balance.
    """
    from accounting.adapters import post_supply_consumption

    committee = charge_committee(work_order)
    if committee is None:
        return None, None

    metas = list(_charge_metas(work_order))
    for meta in metas:
        if not meta.is_reversed:
            return meta.transaction, None

    # Consumption, not job cost: see the "One entry per job" note above for why
    # this is deliberately not ``actual_material_cost``.
    amount = work_order.consumed_material_cost or Decimal("0.00")
    if amount <= 0:
        logger.warning(
            "Work order %s completed on committee-owned asset %s with no "
            "material cost — nothing posted to the ledger.",
            work_order.pk,
            work_order.asset_id,
        )
        return None, NO_COST_WARNING

    # First charge keeps the bare key; each later cycle appends its ordinal, so
    # the family stays greppable and every entry keeps a stable idempotency key.
    cycle = len(metas) + 1
    base = _source_ref_base(work_order)
    source_ref = base if cycle == 1 else f"{base}:{cycle}"

    return (
        post_supply_consumption(
            committee=committee,
            amount=amount,
            source_ref=source_ref,
            asset=work_order.asset,
            created_by=_recorded_actor(actor),
            description=f"Work order materials: {work_order.display_title}",
        ),
        None,
    )


def reverse_work_order_charge(work_order: "WorkOrder", *, actor=None) -> Optional["Transaction"]:
    """Undo the charge when a completed work order is reopened.

    Posts the append-only mirror of the live charge
    (:func:`accounting.services.reverse_entry`) and returns it, or ``None`` when
    this work order has no charge standing — a work order that was never charged,
    or one already reversed. The original entry is never edited or deleted; the
    committee's balance simply returns to where it was.
    """
    from accounting.services import reverse_entry

    transaction = active_charge(work_order)
    if transaction is None:
        return None
    return reverse_entry(transaction, created_by=_recorded_actor(actor))
