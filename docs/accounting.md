# Accounting ledger (architecture)

OMS's accounting subsystem is a **double-entry ledger** built on
[django-hordak](https://django-hordak.readthedocs.io/) `2.0.0`. Phase 1 is the
**engine**: the `accounting` app, the chart of accounts, and a service layer.
Phase 2 wires domain events onto it through **adapters**; the first — charging a
committee for supplies on consume — is documented under
[Committee chargeback](#committee-chargeback--charge-on-consume-phase-2) below.

See [`backend/accounting/README.md`](../backend/accounting/README.md) for the model
map, the `post_entry`/`reverse_entry`/`trial_balance` service API, and the chart of
accounts.

## PostgreSQL is required

hordak relies on **Postgres-only** features and cannot run on sqlite:

- A **deferred constraint trigger** enforces that every transaction's legs sum to
  zero (money in = money out), checked at `COMMIT`.
- Balance reads (`Account.objects.with_balances()`) use a Postgres `get_balance`
  SQL function installed by hordak's migrations.
- hordak's `0002_check_leg_trigger` migration raises `NotImplementedError` on
  sqlite partway through `migrate`.

To turn that late, opaque failure into a clear one, `accounting/checks.py` registers
a Django system check that fails `migrate` / `runserver` / `check` with
`accounting.E001` when the default database is sqlite:

```
OpenMakerSuite now requires PostgreSQL (the accounting ledger uses Postgres-only
features). Set DATABASE_URL=postgres://… or use docker-compose; see docs/accounting.md.
```

CI, the production deployment, and `docker-compose` all already use Postgres. For
**local development**, point `DATABASE_URL` at a Postgres instance, e.g.:

```bash
# via docker-compose (recommended)
docker compose up -d db
export DATABASE_URL=postgres://postgres:postgres@localhost:5432/makerspace_inventory

# or any local Postgres
export DATABASE_URL=postgres://<user>:<pass>@localhost:5432/<db>

cd backend && python manage.py migrate
```

The bare-sqlite dev path (`DATABASE_URL` unset) is no longer supported once the
`accounting` app is installed; `manage.py check` will tell you so.

## Currency configuration

Single currency **USD**, 2 decimal places. In `config/settings.py`:

```python
DEFAULT_CURRENCY = "USD"
CURRENCIES = ("USD",)
HORDAK_INTERNAL_CURRENCY = "USD"
HORDAK_DECIMAL_PLACES = 2
HORDAK_MAX_DIGITS = 20   # see note below
```

`HORDAK_MAX_DIGITS` is **20**, not the 13 originally scoped: hordak's `Leg`
`credit`/`debit` columns back a DB view (`hordak_leg_view`), and PostgreSQL forbids
altering the type of a column a view depends on. Narrowing them to `numeric(13, 2)`
would require dropping and recreating hordak's views inside a migration we own —
disproportionate risk for a Phase-1 foundation. `numeric(20, 2)` (hordak's shipped
width) amply covers USD amounts at 2dp.

## The hordak migration shim (`config/hordak_migrations/`)

hordak `2.0.0` ships migrations whose `Leg` money columns were frozen against a
different `django-money` / `py-moneyed` / `babel` than this repo pins (see
`backend/requirements.txt`). Concretely, its shipped `currency` column carries a
2024 full-currency `choices` list and an `EUR` default, while the installed models
render a USD-only `choices` list and USD default. **This mismatch is inherent to
installing hordak here — it exists regardless of which currency we pick** — and
would fail CI's `makemigrations --check` migration-drift gate the moment hordak is
added to `INSTALLED_APPS`.

We cannot commit a migration into the pip-installed hordak package, so
`settings.MIGRATION_MODULES` redirects hordak's migration module to the first-party
package `config/hordak_migrations/`. That package's `__init__.py` extends its
`__path__` to include hordak's own migrations directory, so Django discovers:

- hordak's shipped `0001..0054` from site-packages, **and**
- our single reconciliation migration `0055_alter_leg_credit_alter_leg_currency_alter_leg_debit`.

The reconciliation migration changes **Django field state only** (`choices` /
`default_currency` are Python-level) — it emits no DDL, so it does not touch the
view-backed money columns. USD-only `choices` keep it stable across future
`django-money` / `babel` bumps. The recorded migration app label stays `hordak`, so
`django_migrations` rows are unchanged.

> hordak is pinned (`==2.0.0`). A hordak version bump must revisit this shim (and
> re-run `makemigrations --check`), because a new upstream `0055+` could collide
> with ours.

## Committee chargeback — charge on consume (Phase 2)

The first domain event wired into the ledger. When inventory is consumed **for a
committee** through the existing `InventoryItem` `log_usage` action, OMS snapshots
the cost and posts a balanced journal entry in the **same DB transaction** as the
stock decrement:

```
DR 5100 Committee supplies expense   (dimension: sig = the committee)
CR 1300 Inventory — Supplies on hand
```

The committee is an `auth.Group` recorded as the debit line's **SIG dimension**
(via the Phase-1 `LegDimension` side-table) — *not* an account per committee. The
cost is an **expense**; settlement / period-close is a later bead.

### The adapter (`accounting/adapters.py`)

Callers never hardcode account codes or hand-write legs. They call one adapter:

```python
from accounting.adapters import post_supply_consumption

txn = post_supply_consumption(
    committee=group,              # auth.Group -> debit line's SIG dimension
    amount=total_cost,            # positive USD Decimal
    source_ref=f"usage:{usage_log.pk}",
    item=item,                    # only used to describe the entry
    created_by=request.user,
)
```

It wraps `accounting.services.post_entry` with the fixed **5100 / 1300** mapping
and `source_type=SourceType.SIG_CHARGE`. It is deliberately reusable by the later
serialized-consume and work-order material-usage charge paths, which share the
same mapping. (No reversal helper yet — `log_usage` has no undo; a
consumption-reversal flow is a future bead.)

### Snapshot + idempotency

- **Snapshot at consume time.** `UsageLog` grew `unit_cost` (a copy of
  `item.unit_cost`), `total_cost` (`unit_cost × quantity_used`), `charged_by`, and
  `charged_group`, plus a `ledger_transaction` FK to the posted entry. The cost is
  copied so a *later* supplier price change never rewrites the books — mirroring
  the ledger's own append-only history. The cost/actor snapshot is taken on every
  consume, even when no committee is charged (harmless record-keeping).
- **Idempotent** on `source_ref=f"usage:{usage_log.pk}"`: because each `UsageLog`
  has a unique pk, replaying a post for the same log returns the original
  transaction instead of double-charging (enforced by `EntryMeta`'s partial-unique
  `(source_type, source_ref)`).

### The no-cost warning

`item.unit_cost` is derived from the primary supplier and can be `None`. If a
committee is given but there is **no cost on file** (`total_cost` null or `≤ 0`),
the committee is still recorded on the `UsageLog` (with `unit_cost = None`) but
**nothing is posted** to the ledger, and the response carries a `warning`:

> committee recorded, but the item has no unit cost — nothing posted to the ledger

### Permissions & backward compatibility

`log_usage` is public (`AllowAny`). Passing `charged_group` **additionally**
requires the caller be staff or an admin of the item's owning group; an
unauthorized or anonymous caller who supplies a committee gets `403`. With **no**
`charged_group` the endpoint behaves exactly as before — same stock math, same
(public) permissions, no ledger entry.

## PO receipt → committee purchasing (Phase 2)

The mirror bookend of the chargeback. When inventory is received against a
purchase order through `receive_delivery` (the `receive` / `mark-delivered`
actions), OMS posts a balanced entry **for each received line whose item is owned
by a committee**, in the **same DB transaction** as the stock increment:

```
DR 1300 Inventory — Supplies on hand   (dimension: sig = the committee)
CR 2000 Accounts Payable
```

The committee is `item.owning_group` (an `auth.Group`) recorded as the debit
line's **SIG dimension**. The amount is `quantity × unit_cost`, where
`unit_cost = po_item.unit_cost_actual or po_item.unit_cost_ordered` — the actual
charged cost wins over the ordered estimate when known. This is the *purchasing*
side of a committee's tab: receiving **fills** on-hand supplies (and incurs a
payable), while consuming later **draws them down** as an expense (the 5100/1300
chargeback above). Vendor payment (DR 2000 / CR Cash) is a future bead — there is
no Cash account in v1.

### The adapter (`accounting/adapters.py`)

```python
from accounting.adapters import post_po_receipt

txn = post_po_receipt(
    committee=item.owning_group,          # auth.Group -> debit line's SIG dimension
    amount=quantity * unit_cost,          # positive USD Decimal
    source_ref=f"po_receipt:{delivery_item.id}",
    item=item,                            # only used to describe the entry
    created_by=received_by,
)
```

It wraps `accounting.services.post_entry` with the fixed **1300 / 2000** mapping
and `source_type=SourceType.PO_RECEIPT`. `receive_delivery` imports it **lazily**
(a local import inside the function) to avoid any `reorder_queue` ↔ `accounting`
import cycle.

### Idempotency & backward compatibility

- **Idempotent** on `source_ref=f"po_receipt:{delivery_item.id}"`: every receipt
  mints exactly one `DeliveryItem`, so its pk keys the entry — re-driving the
  same receipt returns the original transaction instead of double-posting
  (enforced by `EntryMeta`'s partial-unique `(source_type, source_ref)`).
- **Backward compatible.** A line whose item has **no owning committee**
  (`owning_group` is null) or **no unit cost** (the resolved actual-or-ordered
  cost is zero) posts nothing — the stock increment and every other receipt side
  effect behave exactly as before. Only committee-owned, priced lines touch the
  ledger.

## Out of scope (Phase 2+)

Web committee-picker UI + ScanTTY consume-and-charge flow; the committee statement
report; settlement / period-close; vendor / donation / asset adapters;
## Committee settlement / period-close (Phase 2)

The privileged "reset a committee's expenses" — reframed as an **append-only
settlement**. It never edits or deletes the accumulated `SIG_CHARGE` entries;
instead it snapshots the committee's outstanding balance, posts one closing
`SETTLEMENT` entry that zeroes it, and starts the next period at zero, with the
full charge history preserved.

### The balance (`accounting/services.py`)

A committee's **outstanding balance** is the net (debit − credit) of every ledger
leg tagged `sig = committee` on account **5100**:

```python
from accounting.services import committee_balance

owed = committee_balance(group)                    # all-time, account 5100
owed = committee_balance(group, as_of=some_date)   # only entries dated <= some_date
```

Charges (`DR 5100`) push it up; a settlement (`CR 5100`) or a charge reversal
(`CR 5100`, via `reverse_entry`) push it back toward `0.00`.

### The settlement (`accounting/adapters.py`)

```python
from accounting.adapters import settle_committee

txn = settle_committee(
    committee=group,        # auth.Group to close out
    as_of=None,             # optional close date; defaults to today
    reimbursed=False,       # absorb (default) vs. record a receivable
    created_by=request.user,
    note="Q2 close",
)                           # -> Transaction, or None when nothing to settle
```

It computes `balance = committee_balance(committee, as_of=as_of)` and posts one
balanced entry with the committee dimension on **both** legs:

```
CR 5100 Committee supplies expense   [sig = committee]   (removes the accrued expense)
DR <offset>                          [sig = committee]
    offset = 3000 Net assets / Fund balance   (absorbed — the default), or
             1200 Accounts Receivable         (reimbursed=True — records they owe)
```

After it posts, `committee_balance(committee, as_of=as_of)` is `0.00` and the
trial balance still balances (the DR offset equals the CR on 5100). A
**reimbursed** close stops at Accounts Receivable — there is no Cash account in
v1, so the actual cash receipt is a later concern.

- **Nothing to settle** → returns `None`; no empty entry is written.
- **Idempotent per committee + close date** (`source_ref="settle:<id>:<date>"`):
  re-running the same close does not double-post. A committee is settled **once
  per date** — a charge that lands after that date's settlement is closed on the
  next settlement date.
- **Corrections are append-only.** A mistaken settlement is fixed with
  `accounting.services.reverse_entry` (Phase 1), never by editing the entry.

### The endpoint & permissions

`POST /api/accounting/committee-settlement/` (`CommitteeSettlementView`) —
**staff/superuser only** (`IsAdminUser`). This is the destructive-feeling close,
so a SIG admin may *view* its statement but **not** settle. Body: `committee`
(Group id, required), optional `as_of` (date), `reimbursed` (bool), `note` (str).
Response: `{settled_amount, reimbursed, transaction, new_balance, committee}`;
when the balance is already zero it returns `200` with `settled_amount "0.00"`,
`transaction: null`, and a `detail` note (a no-op close is fine, not an error).

## Out of scope (Phase 2+)

Web "settle" button (a later bead); ScanTTY (a privileged financial close is not a
TUI flow); the actual cash receipt of a reimbursement (no Cash account in v1 —
reimbursed stops at AR); PO / vendor / donation / asset adapters;
serialized-consume + work-order material-usage charge paths (they will reuse
`post_supply_consumption`).
## Committee statement report (Phase 2)

The treasurer-readable payoff over the ledger: given a committee (SIG) and a date
range, produce its **statement** — every ledger line attributed to that committee,
with a running balance and period totals — as on-screen **JSON + CSV + PDF**. It
mirrors the asset [cost-recovery report](../backend/inventory/views.py) in
structure (a pure builder, passthrough renderers so `?format=` negotiates, a flat
CSV writer, a reportlab PDF).

### The builder (`accounting/reports.py`)

```python
from accounting.reports import committee_statement

report = committee_statement(
    committee=group,           # auth.Group (SIG)
    start=start_date,          # inclusive date bounds
    end=end_date,
    period="past_month",       # optional preset label; None for a custom range
)
```

It queries `LegDimension.objects.filter(sig=committee, leg__transaction__date__range=(start, end))`
(`select_related` down to the leg's account, transaction, and `EntryMeta`), orders
by date, and emits one row per ledger line:

```
{date, source_type, account_code, account_name, description,
 debit, credit, amount, running_balance}
```

`amount` is the signed net effect on the committee (a **debit increases** the
balance, a **credit decreases** it) and `running_balance` accumulates it. Money is
raw `Decimal` in the builder; the API stringifies it (the JSON contract is decimal
strings), and the CSV/PDF format it.

### Totals, bucketed by source type

`totals` is `{consumed, purchased, settled, net}`:

- **`consumed`** — the `SIG_CHARGE` lines (Bead 1). This is the only bucket that
  populates today.
- **`purchased`** (`PO_RECEIPT`) / **`settled`** (`SETTLEMENT`) — forward-compatible
  and computed **now**, so they light up automatically once Beads 4–5 post those
  committee-attributed entries. Until then they are `0.00`.
- **`net`** — the ending running balance across **all** attributed lines. Because it
  is the running balance (not `consumed + purchased + settled`), it also nets out a
  `REVERSAL` of a charge: `consumed` stays gross, `net` reflects the reversal.

### The endpoint

`GET /api/accounting/committee-statement/`:

- `committee` (required) — the `auth.Group` id. Missing/invalid → **400**; unknown
  → **404**.
- Period: either `period` in `{past_week, past_month, past_year}` (trailing window
  ending today) **or** `start` & `end` (`YYYY-MM-DD`). Neither → 400.
- `format` in `{json (default), csv, pdf}` — DRF content-negotiated via the reserved
  `?format=` query param against passthrough renderers (an unknown format 404s in
  negotiation, the same pattern the cost-recovery report uses).

**Permissions.** Authenticated; **staff/superuser** may read any committee; a
non-staff user must be an **admin of the requested committee**
(`membership.services.is_owning_group_admin`) — this is a *per-committee* gate, so
an admin of SIG X cannot read SIG Y's statement. Otherwise **403**.

## Out of scope (Phase 2+)

Web committee-picker UI + ScanTTY consume-and-charge flow; the **web statement
page** (a follow-on bead, mirroring the cost-recovery generator page); settlement /
period-close; PO / vendor / donation / asset adapters; serialized-consume +
work-order material-usage charge paths (they will reuse `post_supply_consumption`).
