# accounting

Phase 1 of the OMS accounting initiative: a **double-entry ledger core** built on
[django-hordak](https://django-hordak.readthedocs.io/). This is the *engine only* —
it stands and is fully tested in isolation, with **no domain wiring yet** (that is
Phase 2). Nothing in `inventory`, `reorder_queue`, `donations`, etc. writes to the
ledger at this stage.

> **PostgreSQL is required.** hordak's balance enforcement (a deferred constraint
> trigger) and balance views are Postgres-only. Running against sqlite fails a
> Django system check (`accounting.E001`) with an actionable message. See
> [`docs/accounting.md`](../../docs/accounting.md).

## Design

hordak owns the ledger primitives; hordak 2.0 has **no swappable models**, so OMS
attaches its own side-tables rather than editing hordak's:

| hordak | OMS side-table | purpose |
|--------|----------------|---------|
| `Account` (MPTT tree) | — | chart of accounts (`type` on root only, `full_code` set by DB trigger) |
| `Transaction` | `accounting.EntryMeta` (1:1) | journal entry + provenance/audit + idempotency key |
| `Leg` (separate `credit`/`debit` `MoneyField`s) | `accounting.LegDimension` (1:1) | committee (SIG) / asset attribution on a line |

- **Committee/SIG attribution is a per-line dimension** — a nullable `auth.Group`
  FK on `LegDimension`, *not* an account-per-committee.
- Single currency **USD**, 2 decimal places.
- We do **not** expose hordak's own URLs/UI/templates — the API below is OMS-native.

## Service layer (`accounting/services.py`)

The service layer is the only sanctioned way to write to the ledger.

```python
from decimal import Decimal
from accounting.services import Line, post_entry, reverse_entry, trial_balance
from accounting.models import SourceType

# Post a balanced entry (a PO receipt: supplies asset up, payable up),
# attributing the debit line to a committee (SIG = auth.Group).
txn = post_entry(
    lines=[
        Line(account="1300", debit=Decimal("100.00"), sig=woodshop_group),
        Line(account="2000", credit=Decimal("100.00")),
    ],
    source_type=SourceType.PO_RECEIPT,
    source_ref="po:123",          # idempotency key (optional)
    description="PO #123 receipt",
    created_by=request.user,      # optional
)

# Re-posting the same (source_type, source_ref) returns the SAME transaction.
assert post_entry(...same...).pk == txn.pk

# Corrections are append-only: reverse, never edit/delete.
reversal = reverse_entry(txn)     # mirror entry, source_type=REVERSAL, links `reverses`

# Trial balance (Postgres with_balances()); always balances for valid data.
report = trial_balance()          # {"accounts": [...], "total_debit", "total_credit", "balanced"}
```

`post_entry` validates ≥2 lines, exactly one positive `debit`/`credit` per line, and
`Σdebit == Σcredit` — raising `ValueError` *before* any write (hordak's DB trigger is
a backstop). `get_account(code)` resolves an account by its `code`.

## Chart of accounts

Seeded idempotently by the `0002_seed_chart_of_accounts` data migration **and** the
`seed_chart_of_accounts` management command (both call `accounting.chart`). All are
**root** accounts, USD. There is intentionally **no 1000 Cash account** in Phase 1.

| code | name | type |
|------|------|------|
| 1200 | Accounts Receivable | asset |
| 1300 | Inventory — Supplies on hand | asset |
| 1700 | Equipment / Fixed assets | asset |
| 2000 | Accounts Payable | liability |
| 3000 | Net assets / Fund balance | equity |
| 4000 | Donation income | income |
| 4200 | Cost-recovery income | income |
| 5100 | Committee supplies expense | expense |
| 5300 | Maintenance & repair expense | expense |
| 5900 | Inventory shrinkage / adjustment | expense |

```bash
python manage.py seed_chart_of_accounts   # idempotent; safe to re-run
```

## API (`/api/accounting/`, staff/superuser only)

| method | path | description |
|--------|------|-------------|
| GET | `/api/accounting/accounts/` | chart of accounts with live balances (`code, full_code, name, type, balance`) |
| GET | `/api/accounting/accounts/{id}/` | one account |
| GET | `/api/accounting/trial-balance/?as_of=YYYY-MM-DD` | trial balance (money as decimal strings) |

Permissions are `IsAdminUser` for all of Phase 1. Committee-scoped read access
comes in Phase 2.

## Tests

`cd backend && pytest accounting` — **requires PostgreSQL** (`DATABASE_URL=postgres://…`).
The suite covers balanced/unbalanced posting, dimensions, idempotency, append-only
reversal, the trial balance, the seeded chart, and the API permissions.
