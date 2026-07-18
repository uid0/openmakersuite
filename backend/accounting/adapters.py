"""Domain → ledger adapters.

Adapters are the seam between OMS domain events (consuming supplies, receiving a
PO, recording a donation, …) and the double-entry engine in
:mod:`accounting.services`. Each adapter encapsulates *one* account mapping so
callers post money without hardcoding account codes or hand-writing balanced
legs — they describe the business event, the adapter builds the entry.

Phase 2 · Bead 1 ships the first one, :func:`post_supply_consumption`.
"""

from decimal import Decimal
from typing import Optional

from django.utils import timezone

from hordak.models import Transaction

from .models import SourceType
from .services import Line, committee_balance, post_entry

#: Committee supplies expense (debited when a committee consumes supplies).
ACCOUNT_COMMITTEE_SUPPLIES_EXPENSE = "5100"
#: Inventory — supplies on hand (credited as stock is drawn down).
ACCOUNT_INVENTORY_SUPPLIES = "1300"
#: Net assets / Fund balance — absorbs a committee's expense on an absorbed close.
ACCOUNT_NET_ASSETS = "3000"
#: Accounts Receivable — records the amount owed on a reimbursed close.
ACCOUNT_ACCOUNTS_RECEIVABLE = "1200"


def post_supply_consumption(
    *,
    committee,
    amount: Decimal,
    source_ref: str,
    item=None,
    asset=None,
    created_by=None,
    date=None,
    description: str = "",
) -> Transaction:
    """Post a committee supplies chargeback and return its ``Transaction``.

    Records that ``committee`` (an ``auth.Group``) consumed ``amount`` (USD) of
    on-hand supplies::

        DR 5100 Committee supplies expense   (dimension: sig=committee, asset=asset)
        CR 1300 Inventory — Supplies on hand

    The entry carries ``source_type=SIG_CHARGE`` and is idempotent on
    ``source_ref`` (see :func:`accounting.services.post_entry`), so replaying the
    same domain event returns the original transaction instead of double-posting.

    Args:
        committee: The ``auth.Group`` charged; recorded as the debit line's SIG
            dimension.
        amount: Positive USD amount to charge (``Decimal``).
        source_ref: Idempotency key for this domain event, e.g. ``"usage:<pk>"``.
        item: Optional ``InventoryItem`` — used only to describe the entry.
        asset: Optional ``inventory.Asset`` recorded as the debit line's asset
            dimension (for later asset-attributed consumption paths).
        created_by: Optional acting user recorded on the entry's provenance.
        date: Optional posting date (defaults to today via hordak).
        description: Optional human description; a sensible default is derived
            from ``item`` when omitted.

    Returns:
        The posted (or pre-existing, when idempotent) ``hordak.Transaction``.

    This adapter is deliberately reusable by the later serialized-consume and
    work-order material-usage charge paths, which will call it with the same
    5100/1300 mapping. (No reversal helper yet — ``log_usage`` has no undo;
    TODO: add one when a consumption-reversal flow lands.)
    """
    if not description:
        description = (
            f"Committee supplies consumed: {item}"
            if item is not None
            else "Committee supplies consumed"
        )

    return post_entry(
        lines=[
            Line(
                account=ACCOUNT_COMMITTEE_SUPPLIES_EXPENSE,
                debit=amount,
                sig=committee,
                asset=asset,
            ),
            Line(account=ACCOUNT_INVENTORY_SUPPLIES, credit=amount),
        ],
        source_type=SourceType.SIG_CHARGE,
        source_ref=source_ref,
        created_by=created_by,
        date=date,
        description=description,
    )


def settle_committee(
    *,
    committee,
    as_of=None,
    reimbursed: bool = False,
    created_by=None,
    note: str = "",
) -> Optional[Transaction]:
    """Settle (period-close) a committee's outstanding balance. Append-only.

    This is the privileged "reset a committee's expenses" reframed as an
    *append-only settlement*: it never edits or deletes accumulated
    ``SIG_CHARGE`` entries. It snapshots the committee's outstanding balance
    (:func:`accounting.services.committee_balance` on **5100**) and posts one
    balanced ``SETTLEMENT`` entry that zeroes it, carrying the committee
    dimension on **both** legs::

        CR 5100 Committee supplies expense   [sig=committee]   (removes the accrued expense)
        DR <offset>                          [sig=committee]
            offset = 3000 Net assets / Fund balance   (absorbed — the default), or
                     1200 Accounts Receivable         (reimbursed=True — records they owe)

    After it posts, ``committee_balance(committee, as_of=as_of)`` is ``0.00`` and
    the trial balance still balances (the DR offset equals the CR on 5100).

    The entry is idempotent per committee **and** close date
    (``source_ref="settle:<committee.id>:<date>"``), so re-running the same close
    does not double-post. When there is nothing to settle (balance is already
    ``0.00``) this returns ``None`` — no empty entry is written.

    Args:
        committee: The ``auth.Group`` to close out.
        as_of: Optional close date; the settlement is dated here and only
            balances accrued on or before it are settled. Defaults to today.
        reimbursed: When ``True``, debit **1200 Accounts Receivable** (the
            committee owes the amount); otherwise debit **3000 Net assets**
            (the space absorbs it). No cash movement in v1 — reimbursed stops
            at AR.
        created_by: Optional acting user recorded on the entry's provenance.
        note: Optional human description for the entry.

    Returns:
        The posted ``SETTLEMENT`` ``Transaction``, or ``None`` when the balance
        was already zero.

    A mistaken settlement is corrected with :func:`accounting.services.reverse_entry`
    (append-only), never by editing this entry.
    """
    balance = committee_balance(committee, as_of=as_of)
    if balance == 0:
        return None

    effective_date = as_of or timezone.now().date()
    offset_account = ACCOUNT_ACCOUNTS_RECEIVABLE if reimbursed else ACCOUNT_NET_ASSETS
    description = note or f"Committee settlement: {committee.name}"

    return post_entry(
        lines=[
            Line(
                account=ACCOUNT_COMMITTEE_SUPPLIES_EXPENSE,
                credit=balance,
                sig=committee,
            ),
            Line(account=offset_account, debit=balance, sig=committee),
        ],
        source_type=SourceType.SETTLEMENT,
        source_ref=f"settle:{committee.id}:{effective_date.isoformat()}",
        created_by=created_by,
        date=effective_date,
        description=description,
    )
