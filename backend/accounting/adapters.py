"""Domain → ledger adapters.

Adapters are the seam between OMS domain events (consuming supplies, receiving a
PO, recording a donation, …) and the double-entry engine in
:mod:`accounting.services`. Each adapter encapsulates *one* account mapping so
callers post money without hardcoding account codes or hand-writing balanced
legs — they describe the business event, the adapter builds the entry.

Phase 2 · Bead 1 ships the first one, :func:`post_supply_consumption`;
Bead 5 adds :func:`post_po_receipt`.
"""

from decimal import Decimal

from hordak.models import Transaction

from .models import SourceType
from .services import Line, post_entry

#: Committee supplies expense (debited when a committee consumes supplies).
ACCOUNT_COMMITTEE_SUPPLIES_EXPENSE = "5100"
#: Inventory — supplies on hand (credited as stock is drawn down; debited as a
#: committee-owned PO is received).
ACCOUNT_INVENTORY_SUPPLIES = "1300"
#: Accounts payable (credited when committee-owned stock is received on a PO).
ACCOUNT_ACCOUNTS_PAYABLE = "2000"


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


def post_po_receipt(
    *,
    committee,
    amount: Decimal,
    source_ref: str,
    item=None,
    created_by=None,
    date=None,
    description: str = "",
) -> Transaction:
    """Post a committee PO-receipt (purchasing) entry and return its ``Transaction``.

    Records that ``amount`` (USD) of stock owned by ``committee`` (an
    ``auth.Group``) was received against a purchase order, adding it to inventory
    on the committee's tab::

        DR 1300 Inventory — Supplies on hand   (dimension: sig=committee)
        CR 2000 Accounts Payable

    This is the mirror bookend of :func:`post_supply_consumption`: purchasing
    fills the committee's on-hand supplies (a payable is incurred), consuming
    later draws them down (an expense is booked). The entry carries
    ``source_type=PO_RECEIPT`` and is idempotent on ``source_ref`` (see
    :func:`accounting.services.post_entry`), so re-driving the same receipt (keyed
    on the ``DeliveryItem``) returns the original transaction instead of
    double-posting.

    Args:
        committee: The ``auth.Group`` that owns the received item; recorded as the
            debit line's SIG dimension.
        amount: Positive USD amount received (``Decimal``), i.e. quantity ×
            unit cost.
        source_ref: Idempotency key for this receipt, e.g.
            ``"po_receipt:<delivery_item_pk>"``.
        item: Optional ``InventoryItem`` — used only to describe the entry.
        created_by: Optional acting user recorded on the entry's provenance.
        date: Optional posting date (defaults to today via hordak).
        description: Optional human description; a sensible default is derived
            from ``item`` when omitted.

    Returns:
        The posted (or pre-existing, when idempotent) ``hordak.Transaction``.
    """
    if not description:
        description = (
            f"Committee PO receipt: {item}" if item is not None else "Committee PO receipt"
        )

    return post_entry(
        lines=[
            Line(
                account=ACCOUNT_INVENTORY_SUPPLIES,
                debit=amount,
                sig=committee,
            ),
            Line(account=ACCOUNT_ACCOUNTS_PAYABLE, credit=amount),
        ],
        source_type=SourceType.PO_RECEIPT,
        source_ref=source_ref,
        created_by=created_by,
        date=date,
        description=description,
    )
