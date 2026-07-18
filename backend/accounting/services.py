"""The OMS accounting service layer.

This is the ONLY sanctioned way to write to the ledger. It wraps hordak's
``Transaction`` / ``Leg`` primitives so callers work in plain ``Decimal`` USD
amounts and get:

- balance validation with a clear ``ValueError`` *before* any DB write (hordak's
  own deferred Postgres trigger is a backstop that only fires at commit);
- idempotency keyed on ``(source_type, source_ref)`` so a source adapter can
  replay the same domain event without double posting;
- committee (SIG) / asset attribution recorded on each line's
  :class:`~accounting.models.LegDimension`;
- append-only reversal (:func:`reverse_entry`) instead of edit/delete;
- a Postgres-backed :func:`trial_balance`.

The ledger is never edited or deleted in place — corrections are new entries.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Sequence, Union

from django.db import IntegrityError
from django.db import transaction as db_transaction

from hordak.models import Account, AccountType, Leg, Transaction
from moneyed import Money

from .models import EntryMeta, LegDimension, SourceType

#: Single supported currency for Phase 1.
CURRENCY = "USD"

#: Amounts are stored at 2 decimal places (matches HORDAK_DECIMAL_PLACES).
_CENTS = Decimal("0.01")


@dataclass
class Line:
    """One side of a journal entry.

    Exactly one of ``debit`` / ``credit`` must be a positive amount. ``account``
    may be a ``hordak.Account`` instance or an account ``code`` string.
    ``sig`` (an ``auth.Group``) and ``asset`` (an ``inventory.Asset``) are the
    optional attribution dimensions recorded on the line.
    """

    account: Union[Account, str]
    debit: Optional[Decimal] = None
    credit: Optional[Decimal] = None
    sig: object = None
    asset: object = None
    description: str = ""


def get_account(code) -> Account:
    """Return the account with the given ``code`` (raises ``Account.DoesNotExist``)."""
    return Account.objects.get(code=str(code))


def _to_amount(value) -> Decimal:
    """Coerce ``value`` to a 2dp ``Decimal`` (USD cents)."""
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    return amount.quantize(_CENTS)


def _resolve_account(value) -> Account:
    return value if isinstance(value, Account) else get_account(value)


def _find_entry(source_type: str, source_ref: str):
    """Return the existing Transaction for this idempotency key, or ``None``."""
    meta = (
        EntryMeta.objects.filter(source_type=source_type, source_ref=source_ref)
        .select_related("transaction")
        .first()
    )
    return meta.transaction if meta is not None else None


def post_entry(
    *,
    lines: Sequence[Line],
    source_type,
    date=None,
    description: str = "",
    source_ref: str = "",
    created_by=None,
    reverses=None,
) -> Transaction:
    """Post a balanced double-entry journal entry and return its ``Transaction``.

    Validates that there are >= 2 lines, each with exactly one positive
    debit/credit, and that total debits == total credits (raising ``ValueError``
    otherwise). When ``source_ref`` is set and a matching
    ``EntryMeta(source_type, source_ref)`` already exists, the existing
    transaction is returned unchanged (idempotent).
    """
    source_type = str(getattr(source_type, "value", source_type))

    # Fast-path idempotency: return the prior entry without touching the ledger.
    if source_ref:
        existing = _find_entry(source_type, source_ref)
        if existing is not None:
            return existing

    lines = list(lines)
    if len(lines) < 2:
        raise ValueError("A ledger entry needs at least two lines.")

    total_debit = Decimal("0.00")
    total_credit = Decimal("0.00")
    prepared = []
    for line in lines:
        has_debit = line.debit is not None
        has_credit = line.credit is not None
        if has_debit == has_credit:
            raise ValueError("Each line must set exactly one of debit or credit.")
        amount = _to_amount(line.debit if has_debit else line.credit)
        if amount <= 0:
            raise ValueError("Line amounts must be positive.")
        if has_debit:
            total_debit += amount
        else:
            total_credit += amount
        prepared.append((line, _resolve_account(line.account), has_debit, amount))

    if total_debit != total_credit:
        raise ValueError(f"Entry does not balance: debits {total_debit} != credits {total_credit}.")

    try:
        with db_transaction.atomic():
            txn_kwargs = {"description": description or ""}
            if date is not None:
                txn_kwargs["date"] = date
            txn = Transaction.objects.create(**txn_kwargs)
            for line, account, has_debit, amount in prepared:
                money = Money(amount, CURRENCY)
                leg = Leg.objects.create(
                    transaction=txn,
                    account=account,
                    description=line.description or "",
                    **({"debit": money} if has_debit else {"credit": money}),
                )
                if line.sig is not None or line.asset is not None:
                    LegDimension.objects.create(leg=leg, sig=line.sig, asset=line.asset)
            EntryMeta.objects.create(
                transaction=txn,
                source_type=source_type,
                source_ref=source_ref or "",
                created_by=created_by,
                reverses=reverses,
            )
            return txn
    except IntegrityError:
        # Lost an idempotency race on uniq_entry_source — another writer created
        # the same (source_type, source_ref) first. Return the winner.
        if source_ref:
            existing = _find_entry(source_type, source_ref)
            if existing is not None:
                return existing
        raise


def reverse_entry(
    transaction: Transaction,
    *,
    created_by=None,
    date=None,
    description: str = "",
) -> Transaction:
    """Post the mirror image of ``transaction`` (append-only reversal).

    Each leg's debit/credit is swapped, its account and dimension copied. The
    reversal carries ``source_type=REVERSAL`` and ``reverses=transaction`` and
    is idempotent (``source_ref="reverse:<uuid>"``), so reversing twice returns
    the same reversal. The original entry is never edited or deleted.
    """
    dims = {d.leg_id: d for d in LegDimension.objects.filter(leg__transaction=transaction)}
    lines = []
    for leg in transaction.legs.select_related("account").all():
        dim = dims.get(leg.id)
        sig = dim.sig if dim is not None else None
        asset = dim.asset if dim is not None else None
        if leg.debit is not None:
            lines.append(
                Line(
                    account=leg.account,
                    credit=leg.debit.amount,
                    sig=sig,
                    asset=asset,
                    description=leg.description,
                )
            )
        else:
            lines.append(
                Line(
                    account=leg.account,
                    debit=leg.credit.amount,
                    sig=sig,
                    asset=asset,
                    description=leg.description,
                )
            )

    return post_entry(
        lines=lines,
        source_type=SourceType.REVERSAL,
        date=date or transaction.date,
        description=description or f"Reversal of transaction {transaction.uuid}",
        source_ref=f"reverse:{transaction.uuid}",
        created_by=created_by,
        reverses=transaction,
    )


def committee_balance(committee, *, account_code="5100", as_of=None) -> Decimal:
    """Return a committee's net outstanding balance on ``account_code`` (2dp).

    A committee (an ``auth.Group``) accumulates expense as ``SIG_CHARGE`` debits
    on account ``5100`` (recorded via each line's
    :class:`~accounting.models.LegDimension` ``sig`` dimension). This helper is
    the net (debit − credit) of every ledger leg tagged ``sig=committee`` on
    ``account_code``, optionally limited to transactions dated on or before
    ``as_of``.

    A positive result is outstanding expense the committee has yet to settle; a
    settlement (CR 5100) or a charge reversal drives it back toward ``0.00``.
    Returns ``Decimal("0.00")`` when the committee has no attributed legs.
    """
    legs = Leg.objects.filter(
        dimension__sig=committee,
        account__code=str(account_code),
    )
    if as_of is not None:
        legs = legs.filter(transaction__date__lte=as_of)
    credits, debits = legs.sum_to_debit_and_credit()
    net = debits[CURRENCY].amount - credits[CURRENCY].amount
    return net.quantize(_CENTS)


def type_word(account: Account) -> str:
    """Human account-type word ('asset', 'liability', ...) or '' for a child."""
    if not account.type:
        return ""
    return AccountType(account.type).name


def trial_balance(as_of=None) -> dict:
    """Return the trial balance as of ``as_of`` (a ``date``) or now.

    Shape::

        {
          "accounts": [
            {code, full_code, name, type, debit, credit, balance}, ...
          ],
          "total_debit": Decimal, "total_credit": Decimal, "balanced": bool,
        }

    ``debit`` / ``credit`` are the gross totals posted to each account;
    ``balance`` is the account's net signed balance (via the Postgres
    ``with_balances()`` function). A valid double-entry ledger always has
    ``total_debit == total_credit`` (``balanced=True``).
    """
    accounts = list(Account.objects.with_balances(as_of=as_of).order_by("full_code", "code"))
    rows = []
    total_debit = Decimal("0.00")
    total_credit = Decimal("0.00")
    for account in accounts:
        legs = account.legs.all()
        if as_of is not None:
            legs = legs.filter(transaction__date__lte=as_of)
        credits, debits = legs.sum_to_debit_and_credit()
        debit_amount = debits[CURRENCY].amount.quantize(_CENTS)
        credit_amount = credits[CURRENCY].amount.quantize(_CENTS)
        bal = account.balance
        balance = (bal[CURRENCY].amount if bal is not None else Decimal("0.00")).quantize(_CENTS)
        total_debit += debit_amount
        total_credit += credit_amount
        rows.append(
            {
                "code": account.code,
                "full_code": account.full_code,
                "name": account.name,
                "type": type_word(account),
                "debit": debit_amount,
                "credit": credit_amount,
                "balance": balance,
            }
        )
    return {
        "accounts": rows,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "balanced": total_debit == total_credit,
    }
