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

## Out of scope (Phase 2+)

Web committee-picker UI + ScanTTY consume-and-charge flow; the committee statement
report; settlement / period-close; PO / vendor / donation / asset adapters;
serialized-consume + work-order material-usage charge paths (they will reuse
`post_supply_consumption`).
