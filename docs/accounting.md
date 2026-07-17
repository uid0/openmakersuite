# Accounting ledger (architecture)

OMS's accounting subsystem is a **double-entry ledger** built on
[django-hordak](https://django-hordak.readthedocs.io/) `2.0.0`. Phase 1 (this
document) is the **engine only**: the `accounting` app, the chart of accounts, and
a service layer. No domain code writes to the ledger yet — that is Phase 2.

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

## Out of scope (Phase 2+)

Committee statement report; posting cost + SIG onto the consumption path
(`log_usage` / `UsageLog`); settlement / period-close; PO / vendor / donation /
asset adapters; any web frontend; any ScanTTY change.
