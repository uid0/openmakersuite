# Project Instructions

## Workflow Roles (Codex vs Claude Code)

Two coding agents work this repo with split responsibilities:

- **Codex** — acceptance criteria author AND PR/backlog manager. Given a feature request, writes `.criteria/<slug>.md` in the format described in `.criteria/README.md`. Does not modify files under `backend/`, `frontend/`, migrations, or tests itself; however, see "Codex PR authority" below for what it IS authorized to do at PR time.
- **Claude Code** — implementer. Reads `.criteria/*.md` and writes code + tests to satisfy every AC. See `CLAUDE.md` for the full role spec and project conventions.

### Codex PR authority

The operator (uid0) has granted codex standing approval to keep the PR backlog clean. Codex may:

- Bring open PRs up to date with `main` (rebase or merge `main` in), resolving trivial conflicts.
- Resubmit PRs after a rebase or fix to keep them mergeable.
- Close PRs that are obsoleted by other landed work or that the operator clearly won't want.
- Merge PRs that are clean (CI green, no review findings, no operator-blocking comments).

Codex should leave PRs open only when there is an actual problem to surface, and should open GitHub issues only when the operator faces a hard decision (architectural choice, scope/cost tradeoff, requirements ambiguity that can't be resolved from existing docs). Routine status, "FYI" notes, and "task done" markers are not issues — those belong in the bead system or PR comments.

The rest of this file applies to both agents.

## Code Style

- There is a .devcontainer environment for editing and running this application in development mode.
- All changes have to pass pre-commit hooks and the github workflows via the act commands that are present on this system.
- This is for a makerspace -- the default action is open, and while there can be workflows on the admin side of things, consider general requests as unauthenticated unless there is a specific need to either acknowledge or take action on this alert.
- Most calls to actions will be either by wehook push to either discord or integration into slack. Keep that in mind when receiving alerts about supplies being out or areas that may need attention from either the cleaning staff or the logistics/supply team.
- Keep in mind that you may be working in a context local to the developer's machine, or inside the .devcontainer. When writing scripts, assume that the developer's machine is running zsh or bash, and that the .devcontainer runs bash. When running scripts, make sure that you're running inside the devcontainer or on the developer's system.
- The developer does approve some actions manually, so please don't assume that the changes you've asked for are immediately ready for use.
- Place all shell scripts in the ./scripts/ directory

## Architecture

- Follow the repository pattern
- Keep business logic in service layers
- Always provide a reasonable default when creating entries. We want a good out of the box experience for both developers as well as for new users.
- Always write appropriate unit, integration, and end-to-end tests using the native language tools and playwright if needed.
- You don't need to create a markdown file for the things that you've done in the repository. Feel free to summarize those changes in the AGENT's file when they would be beneficial for either a human developer, you, or other development agents in the future.
- Always use black, isort, flake8 for python code to make sure that your code is complianct with the tools that we Lint and CI with.

## Backend

- **Django Version**: Currently using **Django 6.0.7** (see `backend/requirements.txt`; migration headers from 0108 onward record it too)
- **`CheckConstraint` uses `condition=`, not `check=`**: Django 6 removed the
  `check=` keyword. Write `models.CheckConstraint(condition=Q(...), name=...)`,
  matching the existing constraints in `inventory/` and `reorder_queue/`.
- Use `python manage.py startapp` to create new apps within your project
- Keep models in `models.py` and register them in `admin.py` for admin interface
- **A new DRF `@action` fails CI until the permission matrix is refreshed**: `config/tests/test_permission_matrix.py` introspects the live URL conf and fails when `backend/config/api_permission_matrix.yaml` drifts from it. [`docs/API_PERMISSION_MATRIX.md`](docs/API_PERMISSION_MATRIX.md) owns the regeneration command and the table row each endpoint needs.
- Use Django's ORM instead of raw SQL queries
- Avoid N+1 queries with `select_related` and `prefetch_related`:

```python
# Good pattern
users = User.objects.select_related('profile')
posts = Post.objects.prefetch_related('tags')
```

- Use Django forms for validation:

```python
class UserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['username', 'email']
```

- Create custom model managers for common queries:

```python
class ActiveUserManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)
```

- Use Django's built-in authentication system
- Store settings in environment variables and access via `settings.py`

### Adding a routed DRF action

Every URL-routed DRF view — including each `@action` on an existing viewset — is
pinned by a snapshot that `config/tests/test_permission_matrix.py` enforces, so
adding one fails that test until the snapshot and its Markdown row are refreshed
in the same change. The regeneration command and the YAML-wins rule live in
[`docs/API_PERMISSION_MATRIX.md`](docs/API_PERMISSION_MATRIX.md) under "Drift
detection".

A hand-rolled `@action` bypasses its serializer on the way in but usually still
serializes its response through one. Anything you read from `request.data` and
write to a non-text model field must be coerced first — e.g.
`serializers.DateField().to_internal_value(raw)` — and coerced *outside* the
`transaction.atomic()` block, so a bad value is a clean 400 before anything is
written. Skipping this persists the row correctly but leaves a `str` on the
in-memory instance, and the response serializer then 500s *after* the commit,
so a retrying operator accumulates duplicate rows. Worked examples:
`inventory` `generate_work_order` and `reorder_queue` `confirm_order`, pinned by
`inventory/tests/test_generate_work_order_due_date.py` and
`reorder_queue/tests/test_po_confirm_expected_delivery_date.py`.

The companion rule on the same read: a key the client did **not** send is not a
supplied `null`. `request.data.get("field")` collapses the two, so a hand-rolled
action that assigns the result unconditionally silently erases whatever the
record already held whenever a client posts a partial (or empty) body — and the
web app posts no body at all on several of these actions. Gate the write on
`if "field" in request.data`, and where the service needs to tell "unsupplied"
from "explicitly cleared" give it an `UNCHANGED` sentinel default rather than
`None` (`reorder_queue.services.purchase_orders.confirm_order`). The convention
is stated in `ReorderRequestViewSet.mark_ordered`'s docstring and pinned by
`reorder_queue/tests/test_po_confirm_preserves_expected_delivery_date.py`.

### Which supplier an item is bought from: one derivation, orderable only

`inventory.services.supplier_selection` is the ONE answer to "which supplier for
this item". Everything else reads it — `InventoryItem.primary_item_supplier` and
the seven flat compat properties, `item_metrics` (the pinned ScanTTY contract),
the order pad and the PO-building screens. Do not re-derive it: three copies of
`ORDER BY -is_primary, unit_cost` had already drifted apart before op-2rsp
collapsed them.

The forecasts (`component_forecast`, `demand_forecast_engine`) resolve their own
lead time and are NOT on this derivation, permanently. Both read EVERY link,
inactive and discontinued included, because "how long does a replacement take to
arrive" is answered by whoever last shipped one — and routing them through the
orderability filter drops a dead-vendor item off the demand-forecast report and
the nightly digest entirely. op-c1ke pins that with two behavioural tests in
`inventory/tests/test_alert_suppression.py` —
`test_the_serialized_forecast_keeps_a_dead_vendors_lead_time` and
`test_an_item_whose_only_supplier_died_reaches_the_report_and_the_digest`, both
of which fail if the filter is reintroduced; see "The alert-suppression class"
below.

The rule is three things, in this order:

1. **Eligibility.** Only orderable links are candidates — a link that is not
   `is_active`, or that is `is_discontinued`, is never the answer. That includes
   one an operator flagged primary, because `mark_discontinued` deliberately
   leaves `is_primary` set.
2. **The gate.** An orderable link flagged primary wins OUTRIGHT and is never
   scored. A flagged primary is not a term in a sum — any weight can be outbid,
   and then the operator's explicit choice is merely expensive rather than
   binding. Do not "fix" a selection problem by adjusting a bonus.
3. **The score.** Only when nothing orderable is flagged does `score_candidate`
   rank the candidates on cost and lead time.

Ask `select_supplier` / `select_suppliers_for` when you must explain yourself to
an operator: they separate `NO_SUPPLIERS` from `NONE_ORDERABLE`, which are
different facts needing different actions, and flag when the operator's own
choice was the row that got skipped. `primary_item_supplier` is the same answer
with the reason dropped.

Filtering happens in Python, on `item_suppliers.all()`, so the prefetch cache
still serves it — a fresh `.filter()` reintroduces the per-row N+1 that #882
removed and that `docs/API_LIST_CONTRACT.md` bounds in CI. The cost yardstick
(`average_orderable_unit_cost`) is computed in Python for the same reason.

**The scoring weights are a product decision, not an implementation detail.**
They came from the rival rule this replaced and are pinned as they stand;
`inventory/tests/test_supplier_scoring.py` asserts each one and names five
places the judgement is questionable (`REPORTED, NOT FIXED`) — most importantly
that an unpriced supplier can never beat a priced one, because a missing price
scores like a bad price. Two of the five are the same falsy-guard mistake: a
`unit_cost` of 0 and an `average_lead_time` of 0 both read as "unknown", so the
best possible price and the best possible lead time are each graded as the
worst. Retuning any of them needs a captain decision, and the tests will fail
until it is deliberate.

`PurchaseOrderViewSet._find_best_supplier` is now a thin delegation, kept for its
call site's readability. It has no rule of its own.

Aggregates that value stock rather than choose a vendor (`lowest_unit_cost`,
`total_value`, the report averages) deliberately still read every link.

`InventoryItem.current_cases` is EXCLUDED from this supplier derivation, and
still is. Deriving from the READERS OF A SYMBOL is not the same as deriving from
the QUESTION BEING ASKED: it asks how many units are in a box on the shelf,
which has nothing to do with who we buy from. See the pack-size derivation
below, which now owns that answer.

### How many units are in a box: one derivation, three states (op-c1ke)

`inventory.services.pack_size` is the ONE answer to "how many base units are in
one package of this item". It is the sibling of `supplier_selection` and the
same discipline: one module, one interpretation of the column, entry points that
differ only in WHICH row they ask.

`pack_size_of(link)` is the only place `ItemSupplier.quantity_per_package` is
turned into an answer; `shelf_pack_size` and `order_pack_size` are the two
item-level entry points, and they ask genuinely different questions.

**That module's docstring owns the mechanics — read it before touching any of
this.** The three states plus `order_pack_size`'s fourth and what each one tells
an operator; the judgement that **a recorded 1 is KNOWN** and why
`declares_a_case` is the separate question; which link each entry point consults
and why filtering `shelf_pack_size` for orderability is what suppressed a
low-stock alert in op-2rsp round 1; and the query budget `order_pack_size` rides
by reading the memoised `InventoryItem.primary_item_supplier`. All of it lives
there. Do not restate it here — fix it there.

What is worth knowing before you open it: the states are an INTERNAL
distinction. `PackSize.state` does not reach the wire, and both web surfaces say
only "case size unknown" for every one of them. They earn their keep by stopping
`order_pack_size` collapsing `NO_SUPPLIERS` with `NONE_ORDERABLE`, and by
keeping each unknown's CAUSE available to the surface that will word it — filed
as separate follow-up. No flag moves between them.

`inventory/tests/test_pack_size_single_owner.py` is the build gate: it walks
every non-test module under `backend/` with the AST and pins the exact set of
direct reads of the column. A new one anywhere fails until it goes through the
derivation or is added to that allowlist with a reason. The allowlist holds only
the column's own definition, verbatim copies (`PriceHistory`, the payload
fields) and the write path — never a derivation.

**The gate is BACKEND-ONLY.** It walks `backend/` and nothing else, so a
frontend reader of `quantity_per_package` is NOT covered and does not fail the
build. That is precisely how `ScanPage.tsx`'s reorder form kept multiplying by a
recorded 0 after the backend readers were all moved onto the derivation; it was
found by review, not by the gate, and is fixed in the page itself. Extending the
scan to frontend sources is filed as separate follow-up. The "a reader added
later fails the build" criterion holds for backend readers only — read it that
way, and do not assume a green suite says anything about `frontend/`.

### The alert-suppression class: CLOSED (op-c1ke)

A value made honestly `None` gets collapsed by downstream arithmetic or a
fallback into a confident, OPTIMISTIC answer — which inverts a boolean and
suppresses an alert on exactly the item that most needs one. The rule, in one
sentence: **a value the system does not know must never be presented, computed
with, or compared against as though it were a known number — and must never
make an item look adequately stocked.**

`inventory/tests/test_alert_suppression.py` is where this class lives. Every
test there is labelled BEFORE/AFTER (a flag that moved) or CONTROL (one that
must not), against the invariant that let it ship: *no item's alerting or
flagging behaviour changes versus base EXCEPT where base was suppressing an
alert because a value was unknown.*

What moved, and what deliberately did not:

1. **`current_cases` — FIXED, flags moved.** No usable pack size fell through to
   "1 unit per package", so raw base units read as a case count. It is now
   `None`, and `needs_reorder` judges such an item in the unit that CAN be
   counted OR by base's own comparison, kept so an unknown may ADD a flag but
   can never REMOVE one. Be precise about how far that closes the split brain:
   the property and the query agree exactly where
   `minimum_cases <= minimum_stock`, which is the shape where they visibly
   disagreed. Where `minimum_cases > minimum_stock` the property still flags an
   item `low_stock_q` does not match — the PRE-EXISTING divergence direction,
   preserved deliberately because closing it the other way would delete an alert
   base raised — and `reorder_threshold` names `max(minimum_stock,
   minimum_cases)` for this shape so a badge and the threshold line beside it
   name the same boundary. The disjunction, and why each of those holds, is
   spelled out where it lives: the comment at that branch of
   `InventoryItem.needs_reorder` and the docstrings on
   `packaging.reorder_threshold` and `packaging.low_stock_q`.
2. **`component_forecast`'s `reorder_point` — expression fixed, NO flag change.**
   The row now says `lead_time_known: false` and the number is a stated LOWER
   BOUND (safety stock alone) rather than a horizon at a fabricated zero-day
   wait.
3. **`demand_forecast_engine` — expression fixed, NO flag change.** The
   threshold falls back to the due date itself.

Why 2 and 3 change no flag, and this is the load-bearing part: `average_lead_time`
is non-nullable with a default, so ANY link supplies an estimate — **a
discontinued one included**. The entire population reaching "no lead time known"
is therefore items with NO supplier link at all. Flagging that population
regardless of what a lead time would have said turns a DATA GAP into a permanent
alert, which is exactly op-2rsp round 4's failure: flooding the surface until
people ignore it suppresses alerts too. `NO_SUPPLIERS` (a data gap) and
`NONE_ORDERABLE` (unbuyable) point in OPPOSITE directions and must stay apart
everywhere. Do NOT route `_lead_time_days_by_item` through the supplier
derivation — `test_the_serialized_forecast_keeps_a_dead_vendors_lead_time` and
`test_an_item_whose_only_supplier_died_reaches_the_report_and_the_digest` in
`inventory/tests/test_alert_suppression.py` pin that, and both fail if the
filter comes back. That function's own docstring carries the rest of the
reasoning; read it before changing anything there.

`current_cases` is nullable on the wire. Every consumer moved in the same commit:
`InventoryItemSerializer` (`allow_null`), the three web sites that called
`.toFixed(1)` (`InventoryList`, item detail, scan page) and `types/index.ts`.
ScanTTY's `CurrentCases` was already a nil-checked `*float64`; its `case_size`
was already `*int` and reads 0 as `null` now — a cross-project VALUE change,
named as one. Round 5 shipped this null against untyped consumers and blanked
two member-facing pages; that is why the consumer set is DERIVED, not recalled.

**The MONEY half of the class is now CLOSED too** — `unit_cost or 0` in
`reorder_queue/views.py`, `unit_cost or Decimal("0.00")` in
`purchase_orders.create_purchase_order` and `line_entry.default_unit_cost`'s
`Decimal("0.00")` were the sites this section filed as
`oms-falsy-zero-money-guards`. See "What a price costs" below. The scoring's own
falsy guards (`test_supplier_scoring.py`, REPORTED NOT FIXED, retuning reserved
to the captain) stay open and are named there. `get_expected_delivery_date`'s
`and self.average_lead_time` — where a KNOWN zero-day lead time yields no date —
was filed here as "the same shape", and it is: but it moves a DATE, not a money
figure, so it was outside the money branch's invariant and is STILL OPEN.

Three more, found by this branch's sweeps and deliberately NOT fixed here:

1. **`ScanPage`'s anonymous auto-submit misdescribes a KNOWN case size.** The
   sentence reads through `reorder_display`, so an item whose case size is
   unknown is now correct, but for a KNOWN case size it says "N cases" while
   `frontend/src/pages/ScanPage.tsx` posts `quantity: item.reorder_quantity` in
   BASE UNITS. Pre-existing, and about a value the system DOES know, so it is
   outside the alert-suppression class this branch closes. Filed separately.
2. **Frontend readers of `quantity_per_package` outside `ScanPage`** — the same
   falsy-zero pack-size class, all pre-existing, none touched here.
   `PurchaseOrderFormPage.tsx` has eight `item.quantity_per_package || 1` sites
   — in `loadReorderData`, `updateItemQuantity`, `updateItemCases`,
   `updateItemUnitCost`, `updateItemCaseCost`, `caseCostPlaceholderFor`,
   `handleProductSearch` and `handleSubmit` — plus four unguarded reads in its
   selected-items table (three `quantity_per_package > 1` branches and the
   "N cases × N units/case" summary line beside them);
   `SupplierRelationshipForm.tsx` coerces a typed 0 to 1 on the WRITE path, in
   the pack-size input's `onChange`
   (`quantity_per_package: Number(e.target.value) || 1`), silently changing what
   an operator typed. Those counts are as of this branch — grep the expressions,
   do not trust the numbers. These are FRONTEND sites and the pack-size build
   gate walks `backend/` only, which is why none of them fails a build today.
3. **Extending the pack-size build gate to frontend sources**, the follow-up
   named above with the gate's backend-only scope. It sits beside 2 because 2
   is the population it would cover: nothing gates a frontend reader until it
   exists.

### What a price costs, and whether we know: one derivation (op-9m2v)

`inventory.services.pricing` is the ONE answer to "what does one unit, or one
package, cost from a supplier — and do we know?". The third single-owner
derivation, after `supplier_selection` and `pack_size`, and the MONEY half of
the falsy-guard class the section above closed for alerting. The rule, in one
sentence: **a price the system does not know must never be presented, summed, or
compared as a real number; a recorded price of zero is a KNOWN price and must be
treated as one.**

**That module's docstring owns the mechanics — read it before touching any of
this.** The four states and what each tells an operator; why a recorded `0.00`
is a price and `or` can never express that; which link each entry point
consults; `PriceRollup`, and why a total reports what it could not price.

What is worth knowing before you open it:

- **A makerspace really does get things for nothing.** Donated stock, free
  samples, internal transfers. `unit_cost` of `0.00` is real and not rare, and
  `or` / `if cost:` cannot tell it from a `NULL`. Every guard spelled that way
  got one of the two cases wrong — an unpriced supplier costed a purchase-order
  line at nothing, and a free supplier read as unpriced.
- **Unlike `PackSize.state`, these states reach the wire.** `reorder_data` and
  `create_optimized_order` carry `unit_cost_state` / `unit_cost_detail` beside
  each unpriced line, because a purchaser is owed the cause and the remedy.
  `pricing.explain` is the one place that sentence is written.
- **Totals say what they left out.** Every order-shaped payload now carries
  `unpriced_item_count` and `estimated_total_is_partial` beside its
  `estimated_total`. A total that was silently wrong becoming visibly
  incomplete is the point.
- **`unit_cost_ordered` stays NON-NULLABLE, and the write paths refuse instead.**
  `create_purchase_order` and `line_entry.add_line_item` raise rather than
  storing a fabricated `0.00` — the same refusal the asset and freeform branches
  of `create_purchase_order` already made ("unit_cost is required when
  purchasing asset X"); the inventory branch was the odd one out. Both messages
  name the two remedies (send `unit_cost`, or price the supplier link), and the
  web form blocks first so the operator is told before the 400.
- **The supplier scoring is deliberately NOT fixed here.**
  `score_candidate`'s `if link.unit_cost and average_unit_cost` is the same
  mistake, so a free supplier can never win on price while its `0.00` still
  drags the yardstick. Repairing it changes which supplier the system picks,
  which moves money for a reason that is not "base presented an unknown price as
  a real number". Captain-reserved, filed as `oms-supplier-scoring-weight-flaws`.

`inventory/tests/test_price_single_owner.py` is the build gate, the twin of the
pack-size one: it walks every non-test module under `backend/` with the AST and
pins the exact set of direct reads of `unit_cost` / `package_cost`. It adds
`Coalesce` and the aggregates to the scanned wrappers, because
`Sum(F("current_stock") * Coalesce("unit_cost_value", Value(0)))` in
`inventory.views`'s stock-value reports is `unit_cost or 0` written in SQL.
**Two honest limits:** the walk is `backend/`-only (a frontend price reader is
not gated, exactly as `quantity_per_package`'s is not), and `unit_cost` is a
column name on five models here, so the allowlist has to say per entry which
model a read is on. `inventory/tests/test_price_guards.py` owns the behaviour,
every test labelled BEFORE/AFTER or CONTROL against the invariant *no money
figure changes versus base EXCEPT where base was presenting an unknown price as
a real number.*

**A falsy guard on a price is only a bug where the value is falsy at zero.**
Backend-side that is easy: the value is a `Decimal` and `Decimal("0.00")` is
falsy, so `if cost:` in `reorder_queue/services/receiving.py` was a real defect
— it billed a committee the ORDERED price for a line the vendor had comped.

**Frontend-side, the answer is decided by the SERIALIZER FIELD, not the field
name — measure it, do not reason about it.** Two rounds of this review recorded
the wrong answer here, in opposite directions, because both reasoned from the
attribute name. The rule, once, relied on everywhere below:

| the serializer field | wire type | truthiness guard |
| --- | --- | --- |
| a real model `DecimalField` on a `ModelSerializer` | STRING (`"0.00"`) | SAFE — `"0.00"` is truthy |
| a model PROPERTY named in `Meta.fields` with no explicit declaration | NUMBER (`0`) | BUG |
| a `SerializerMethodField` returning a `Decimal` | NUMBER (`0`) | BUG |

A property named in `Meta.fields` gets `build_property_field`, which makes a
`ReadOnlyField`; that hands the raw `Decimal` straight to
`rest_framework.utils.encoders.JSONEncoder`, which returns `float(obj)`.
`COERCE_DECIMAL_TO_STRING` only ever applies to an actual `DecimalField`.

So the SAME attribute name has two wire types on this project:
`ItemSupplier.unit_cost` is a model field and arrives as `"0.00"`, while
`InventoryItem.unit_cost` is `order_unit_price(self).amount` — a property — and
arrives as `0`. `Kit` inherits the property kind from `InventoryItemSerializer`;
`KitSummary.unit_cost` is a method field, so also a number.

A falsy number is worse than a falsy string in JSX specifically: `{0 && <X/>}`
evaluates to `0`, which React RENDERS, so the guard both drops the row and
prints a stray "0" where the price belonged. Numbers also need `.toFixed(2)` at
the render site — `` `${5.1}` `` is `"5.1"`, not `"5.10"` — which a decimal
string never did.

`PurchaseOrderPage.tsx`'s `unit_cost_actual` is the string kind and its
truthiness guard is CORRECT; it is pinned as a CONTROL in
`PurchaseOrderPage.test.tsx` so it fails if the field is ever parsed to a
number. Every guard on the number kind is fixed, and the declared types in
`frontend/src/types/index.ts` now say `number | null` so the next reader is not
misled the way these two rounds were.

"The real price PAID per unit" is a different fact with its own older owner,
`work_order_purchase_bridge.purchase_line_unit_cost` (actual when recorded, else
ordered, spelled `is None`). Read it; do not write a second `actual or ordered`.

`reorder_queue/tests/test_estimated_cost_single_owner.py` is the SECOND gate on
this branch, and it exists because a hand-counted reader list is a claim that
goes stale. `ReorderRequest.estimated_cost` — the money face of the derivation,
`Decimal("0.00")` for a free item and `None` only when unpriced — had its reader
set enumerated by hand TWICE and it was incomplete BOTH times: four readers were
named here as complete, and review then found three more on the PUBLIC
`AllowAny` transparency endpoint. **That gate is now the authority on the reader
set; do not re-hand-count it.** It walks `backend/` with the AST exactly as the
price gate does, and its allowlist names, per entry, which of the THREE models
the read is on — `ReorderRequest.estimated_cost` (nullable, this branch's),
`PurchaseOrderItem.estimated_cost` (non-nullable, so a falsy guard on it was
always wrong) or `MaintenanceItem.estimated_cost` (a maintenance budget, a
different fact). Same two honest limits as the price gate: `backend/`-only, and
the AST cannot tell the three models apart, which is why the reason must.

**Three things the earlier passes got wrong, all about CONSUMERS.** Recorded so
the next derivation sweeps for the same shapes:

- **A value becoming nullable has backend readers too.** `total_value` going
  `Decimal("0")` -> `None` was swept across the frontend and ScanTTY but not
  across `backend/`, and `dashboard.views.get_inventory_summary`'s
  `sum(item.total_value for ...)` folded the `None` into an int accumulator —
  a 500 on a public endpoint. It sums through `PriceRollup` now and reports
  `items_without_price` beside an unchanged total. Sweep BOTH sides.
- **The "all but one site" shape.** Where a payload gains an honesty count or a
  guard is respelled, find every twin. The browser-side CSV export got
  `items_without_price` and the server-side one did not; the price-trend
  report's cost columns became nullable and its CSV export still formatted them
  with `:.2f` (a `TypeError`, so a 500); `ReorderRequest.estimated_cost` started
  returning a real `Decimal("0.00")` and its readers re-collapsed it to
  "unknown" with the old falsy guard. `requests/by_supplier/` now carries
  `unpriced_item_count` / `estimated_total_is_partial` like every other
  order-shaped payload, and the admin dashboard's supplier modal renders them.
- **When a hand sweep misses TWICE, build the gate instead of sweeping a third
  time.** That is what `test_estimated_cost_single_owner.py` is. A third
  enumeration would have been the same move that had already failed twice.

**MONEY FIGURES AND PAYLOAD CLAIMS MOVED BY THE REVIEW ROUNDS**, each with the
screen or payload that shows it. The branch invariant requires this list to be
complete, and it was twice not:

- Purchasing price-trends **CSV export** (`GET /api/reorders/reports/purchasing/
  export/?type=price_trends`) — the three cost cells: `TypeError` (an unhandled
  500) -> an EMPTY cell for an unknown price, `0.00` for a recorded zero.
  And `price_change_percentage`: `""` -> `"0.00%"` for a price that genuinely
  did not move, because the falsy guard exported a real 0% change as the same
  blank an INCOMPUTABLE percentage gets.
- Purchasing price-trends **SCREEN** (`/reports/purchasing`) — the SERVER CSV
  above is only one of three surfaces and for four rounds it was the only one
  named. The Min / Max / Latest Unit Cost table cells:
  `$${item.min_unit_cost.toFixed(2)}` -> `money(...)`, so an unknown price now
  reads `—` where base read `$0.00` (base's server sent a literal `0`). A
  supplier that genuinely charges nothing still reads `$0.00`.
- Same screen, the SORT ORDER of those three columns — not a figure, but a
  user-visible reordering. `sortData`'s `a < b` let JavaScript coerce `null` to
  `0`, so a price nobody recorded sorted in among the cheapest. Unknowns now
  sort LAST in both directions; a real `0` still sorts as the cheapest real
  price. Base ordered them the same way only because the values were literally
  `0`, so nothing regressed — this is the one place on that screen where `null`
  and `0` still behaved alike.
- Purchasing price-trends **BROWSER export** (`csvExport.ts`, the client-side
  download from that screen) — the twin of the server CSV named above, and it
  moved the same way: the three cost cells `'$0.00'` -> `''` for a null, and
  `'Price Change %'` `'N/A'` -> `'0.00%'` for a price that genuinely did not
  move. Identical move, different file; naming only the server one is exactly
  the "all but one site" shape recorded above.
- Inventory stock-value **SCREEN** (`/reports/inventory`) — the Total Value
  cell gains a trailing `+` (`$120.00 +`) when the category or location holds
  items nobody has priced, and a new sortable "Unpriced Items" column reports
  how many. The NUMBER is deliberately unchanged; what moved is that a partial
  total now says it is partial.
- Inventory stock-value **BROWSER export** (`csvExport.ts`) — the `Total Value`
  cell `'$0.00'` -> `''` where the server sends no total at all, matching the
  server CSV; and both branches gained the `Unpriced Items` column.
- Supplier detail **price-trend chart** (`PriceTrendChart.tsx`, the only
  consumer of `trends[].price_history[]`) — a snapshot recorded at `0.00` was
  plotted as a GAP in the line and is now plotted as a `$0.00` point.
  `ph.unit_cost || ph.package_cost || null` fell through both real zeros to
  `null`, so the drop to free — the most notable move such a chart can show —
  was the one move invisible on it.
- Inventory stock-value **CSV exports** (`?type=stock_by_category` and
  `?type=value_by_location`) — gained `items_without_price`; the `total_value`
  number is deliberately unchanged.
- Public dashboard tile (`GET /api/dashboard/inventory-summary/`) —
  `total_value`: a 500 -> base's exact number, plus a new
  `items_without_price` beside it.
- Admin dashboard "Requests by Supplier" modal (`GET
  /api/reorders/requests/by_supplier/`) — gained `unpriced_item_count` /
  `estimated_total_is_partial`; the total itself is unchanged.
- Item detail `price_trend_summary` (`GET /api/inventory/items/<id>/`) —
  `{"trend": "no_change", "change_percentage": 0}` -> `{"trend":
  "no_baseline", "direction": "increasing", "change_percentage": null,
  latest_cost, previous_cost, last_updated}` when both prices are known but the
  earlier one is `0.00`. Base presented an undefined percentage as a confident
  zero. `no_baseline` is deliberately NOT `no_data`, which means a snapshot
  records no price at all: three facts, three labels, and `direction` is the
  one thing two known prices still establish when the percentage cannot.
- Public transparency feed (`GET /api/reorders/analytics/transparency/`, no
  auth) — `estimated_cost` on both the `orders` and the `ledger` block:
  `null` -> `0.0` for a donated item, so the community feed no longer says "we
  do not know what this cost" about a cost that is known to be nothing. And
  `cost_variance`: `null` -> the real difference when the ESTIMATE is a known
  `0.00`, which is the one number that says the estimate was wrong.
  `cost_variance` is gated on the same predicate `actual_cost` is, so where the
  ACTUAL cost is a recorded `0.00` it stays `null` exactly as base had it: that
  column is a ratified exclusion (below), and a variance published beside an
  `actual_cost: null` would be a number that can only be true if the actual
  cost were known. The exclusion boundary must not run through one arithmetic
  expression. The `ledger` block needs no such pairing — it carries no derived
  figure, only the two independent fields.
- Public transparency feed, `purchase_orders` block — `estimated_total`:
  `null` -> `0.0` for an order whose every line is donated. That column is
  NON-nullable with `default=Decimal("0.00")`, so `null` was never a true
  answer for it and the falsy guard could only ever mislabel a real zero as
  unknown. The figure derives from `unit_cost_ordered`, which this branch owns
  and writes.
- Public transparency PAGE (`/transparency`, the only consumer of that feed) —
  the "Estimated Cost" and "Cost Variance" rows: hidden, with a stray `0`
  printed into the card, -> `$0.00`. `{order.estimated_cost && <div/>}` fails
  twice over on a numeric `0` in JSX: the row disappears AND React renders the
  `0` itself. Pinned in `TransparencyUnknownCosts.test.tsx`.
- Same page, the LEDGER table's Cost column
  (`formatCurrency(entry.actual_cost ?? entry.estimated_cost)`) — `N/A` ->
  `$0.00` for a donated purchase. The cell itself did not change; it moved
  because the feed beneath it did, which is exactly why a consumer sweep has to
  reach every cell and not just the ones whose code changed.
- Same page, the "Cost Variance" row's TONE and WORDING — all three cases, not
  only the new one. An order that landed exactly on estimate was styled
  `under-budget` and now reads `$0.00 on budget` in a neutral `on-budget`
  class; an overrun read `+$2.00` and now reads `+$2.00 over budget`; a saving
  read `-$2.00` and now reads `-$2.00 under budget`. The zero case is newly
  reachable code — the truthiness guard used to drop the row entirely, so
  `> 0 ? over : under` never had to answer for it — and naming the tone in
  words rather than colour alone is what makes the third state readable at all.
  Three states, three labels, the same rule that keeps `no_baseline` apart from
  `no_data`. All three strings are pinned in `TransparencyUnknownCosts.test.tsx`.
- Outbound reorder webhook (Discord/Slack) and three admin `Est. Cost` columns
  — `null` / `—` -> `$0.00` for a free line.
- Member-facing scan / reorder-request screen (`/scan/<item>`) — the "Package
  cost" and "Unit cost" detail cells, the supplier DROPDOWN option's
  `.../unit` label, the "estimated cost" help text and the Order Summary's
  "Estimated Cost" row: `$0.00` (and a bare `$` on the three label sites) ->
  `— (no price on file)` where the selected supplier link records no cost.
  `parseFloat(supplier.package_cost || '0')` was the last frontend site turning
  an unknown price into a confident zero. A recorded `0.00` still reads
  `$0.00`. The screen already told the truth about an unknown PACK SIZE from
  the sibling branch, so it was reporting how many units honestly and what they
  cost dishonestly; the two now match. An unpriced request is still SUBMITTABLE
  — unlike an unsized one — with a note naming what to add.
  NOTE the boundary, and note that an earlier round drew it WRONG. What is
  safe on this screen is the truthiness on `supplier.unit_cost` — the
  auto-selection filter and the `|| '999'` sort key — because `ItemSupplier`
  rows are real `DecimalField`s: a free link arrives as `"0.00"`, survives the
  filter, sorts first via `parseFloat("0.00") = 0` and IS preselected, and only
  a genuine `null` is dropped or sorted last. That is the behaviour we want.
  The ITEM's own `unit_cost` on the same screen is NOT the same value and was
  never safe; it is fixed in the entry below.
- Item detail, the "Supplied by kits" card (`GET /api/inventory/items/<id>/
  kits/`) — a kit whose primary supplier charges nothing: the price was OMITTED
  entirely (and, because `{0 && <Text/>}` evaluates to `0`, a stray "0" was
  printed into the row) -> `$0.00`, with an unpriced kit now saying so instead
  of looking identical. `KitSummarySerializer.get_unit_cost` is a
  `SerializerMethodField` returning a `Decimal`, which DRF's `JSONEncoder`
  renders as a JSON NUMBER, so truthiness here IS falsy at zero. (An earlier
  round wrote "unlike every string-valued price beside it on that page". That
  was wrong: the item's OWN `unit_cost` row on the same page is a number too —
  see the entry below. The supplier rows are the string-valued ones.) The wire
  format is UNCHANGED
  (base returned a `Decimal` from the same method field): what was wrong was
  `frontend/src/types/index.ts` declaring it `string | null`, which is what
  made the guard read as safe. The type is corrected to `number | null` rather
  than the serializer being switched to a string — the format this branch never
  moved stays put, so no new cross-project contract change needs verifying.
- PO create form, the asset lines — an asset a vendor is DONATING can now go on
  an order. `canSubmit` required `parseFloat(a.unit_cost) > 0`, so a typed `0`
  left the button disabled with nothing saying why, while the freeform half of
  the same form already accepted `>= 0` and the server refuses only a MISSING
  cost. Both halves now share one `hasTypedPrice` helper: a number was entered
  and it is not negative. An empty box still blocks.
- Admin PurchaseOrder changelist, the **Est. Total** column
  (`reorder_queue/admin.py`'s `estimated_total_display`) — `—` -> `$0.00` for
  an order whose every line is donated. The payload twin of this same field was
  fixed one commit earlier; this is the screen that reads it. Same reasoning:
  `PurchaseOrder.estimated_total` is non-nullable-with-default, so the em dash
  — which means "we cannot cost this" everywhere else in that file — could
  only ever be wrong there.
- Two admin **Actual Cost** columns — `reorder_queue/admin.py`'s
  `actual_cost_display` on the PurchaseOrderItem inline and on the
  PurchaseOrderItem changelist: `—` -> `$0.00` for a line receipted at
  `unit_cost_actual = 0.00` with `quantity_received > 0`, where
  `PurchaseOrderItem.actual_cost` returns a real `Decimal("0.00")`. This is the
  DERIVED property, whose `unit_cost_actual` twin in `receiving.py` was the
  branch's own defect — not `ReorderRequest.actual_cost`, which is excluded.
- **Six readers of `InventoryItem.unit_cost` / `Kit.unit_cost`, the PROPERTY-
  backed number** — the shape the two rounds above each mis-recorded. Every one
  guarded a JSON number with truthiness, so a DONATED item (a real `0`) was
  reported as a price nobody had recorded. Listed individually because the
  invariant requires it; a genuinely unpriced item still reads as unknown on
  all six, and every ordinary price is byte-identical:
  - Member scan screen (`/scan/<item>`), the item's own "Unit Cost:" row —
    the row VANISHED and a stray "0" was printed in its place -> `$0.00`. It
    also rendered `${item.unit_cost}` raw, so an unpriced item now reads
    `— (no price on file)` and `5.1` now reads `$5.10`. Uses the page's
    existing `money` helper, widened to take both wire types rather than
    growing a second spelling.
  - Item detail card (`/inventory/items/<id>`), the "Unit Cost:" row — hidden
    with a stray "0" -> `$0.00`, and an unpriced item now says
    `no price on file` instead of nothing, matching the kit card below it.
  - Inventory card grid (`InventoryList.tsx`) — the price line vanished with a
    stray "0" -> `$0.00 per unit`. An unpriced item still shows no price line.
  - Inventory table (`/inventory`), the Cost column — `-` (this table's
    spelling for "unknown") -> `$0.00`.
  - Kit list (`/inventory/kits`), the Unit cost column — `—` -> `$0.00`, and
    the cell interpolated the number raw, so `5.1` -> `$5.10`.
  - Browser-side inventory **CSV export** (`csvExport.ts`), the Unit Cost cell
    — a BLANK cell, the same cell an unpriced item gets, -> `0`.
  NOT touched, because they are the string kind and correct: `supplier.unit_cost`
  on the scan screen, the ItemSupplier rows on the supplier detail page, and
  `InventoryItemMetrics.unit_cost` (an explicit `DecimalField` on
  `InventoryMetricsSerializer` — measured, not assumed).
- Item detail, the "Supplied by kits" card — a SECOND move on the row named
  above, from the same round-8 fix: `${kit.unit_cost}` -> `.toFixed(2)`, so a
  kit priced `5.10` reads `$5.10` rather than `$5.1`. Switching the type from
  the string DRF never sent to the number it does send is what exposed this:
  a decimal string already carried its cent column, a JS number does not. Held
  by a trailing-zero-cent fixture in `InventoryItemSuppliedByKits.test.tsx`;
  the round-8 tests used `89.99`, which cannot fail under the mutation.
- Kit form **WRITE path** (`/inventory/kits/new` and `/inventory/kits/<id>`) —
  `handleSave` sent `unit_cost: unitCost === '' ? '0' : String(unitCost)`, so
  leaving the "Unit cost" box empty STORED `Decimal("0")` on the
  `ItemSupplier`. `order_unit_price` then correctly reported `PRICE_KNOWN`
  about a price nobody had given, and the two rows fixed the round before —
  the kit list's Unit cost column and the item detail "Supplied by kits" card
  — stated as fact that the vendor gives the kit away. The branch's own rule
  inverted at a write path: "a recorded price of zero is a KNOWN price" is
  only true if a blank box never becomes one. Now sends an explicit `null`,
  and the link stores NULL. No displayed figure moved for any kit that already
  had a price; what moved is that a kit saved with a blank cost reads as
  unpriced instead of free. This is the money twin of the
  `SupplierRelationshipForm.tsx` pack-size write-path collapse, and it takes
  the same side as `unit_cost_ordered`'s write, which REFUSES rather than
  fabricating — here NULL is a legal answer, so storing it is enough.
  `null` needs no serializer change: `supplier_terms` is a plain `DictField`,
  whose default `_UnvalidatedField` child has `allow_null = True`, so the
  `None` reaches `_apply_supplier_terms` intact and its `if key in terms`
  comprehension puts it in `defaults` — measured, not assumed.
  **That limit is now CLOSED** — the round that recorded it as unfixable was
  wrong about the cost. See the supplier-terms owner below: clearing a recorded
  price now sticks, because naming one cost clears its twin so the derivation
  has nothing stale to restore. The CONTROL that pinned the limitation is
  rewritten as a BEFORE/AFTER that pins the fix.
  NOTE the seeding path, checked on every route in: `applyKit` seeds
  `supplier_sku` and `unit_cost` but NOT `supplierId`, and the payload only
  carries `supplier_terms` when `supplierId && supplierSku`. So an edit that
  does not retype the supplier id sends no terms at all and cannot erase a
  price; the blank box is only ever read when the operator is actively
  entering terms. Pinned as a CONTROL.
- Kit **purchase terms re-save** (`PATCH /api/inventory/kits/<id>/`, shown on
  the kit list's Unit cost column and the item-detail "Supplied by kits" card)
  — `_apply_supplier_terms` forced `quantity_per_package = 1` into every
  `update_or_create`, and this WAS a money figure, which the previous round
  recorded wrongly. `ItemSupplier.save` re-derives
  `unit_cost = package_cost / quantity_per_package` whenever `package_cost is
  not None`, so the reset carried the price with it. Worked path: a link
  records `unit_cost 5.00`, `package_cost 30.00`, `quantity_per_package 6`; the
  operator opens the kit, picks the supplier from the dropdown (required for
  `supplier_terms` to be sent at all) and saves, touching no cost field ->
  `defaults` carried `quantity_per_package: 1` -> `save()` recomputed
  `unit_cost = 30.00 / 1` -> a 6-pack at $5.00 became a 1-pack at **$30.00** on
  both screens. FIXED, and contained: the `setdefault` line is simply removed.
  `ItemSupplier.quantity_per_package` is already
  `PositiveIntegerField(default=1)`, so a CREATE still stores 1 from the model
  default while an UPDATE now leaves the recorded pack size — and therefore the
  derived price — alone. Nothing in `inventory.services.pack_size` or its gate
  is touched; the pack-size derivation itself is untouched and still owned
  there. The previous round's sentence calling this "a non-money figure" was
  false and is replaced by this entry.
- **`package_cost` in `_apply_supplier_terms`, REPORTED NOT FIXED** — the same
  `null=True` shape as `unit_cost`, so NULL is a real answer for it, but the
  kit form never sends the key at all, so nothing fabricates it today.
- **The kit / item supplier-terms WRITE path now has ONE owner**,
  `inventory.services.suppliers.write_supplier_terms`, and three separate
  defects collapse into it. Every caller used to hand-roll
  `ItemSupplier.objects.update_or_create(defaults=<partial dict>)` against a
  model whose `save()` DERIVES `unit_cost` and `package_cost` from each other,
  and a partial write always loses that fight in one of two directions — the
  stale sibling column overwrites what the operator typed, or a column `save()`
  just derived is dropped by `update_or_create`'s `update_fields` restriction.
  The owner takes intent explicitly via the `UNCHANGED` sentinel (the same
  convention `reorder_queue.services.purchase_orders.confirm_order` uses, so
  "not supplied" stays distinct from "explicitly cleared"), treats the two costs
  as ONE fact, saves in full rather than with a restricted `update_fields`, and
  never defaults `quantity_per_package` on an update. The derivation itself is
  UNTOUCHED and still lives in `save()`; the owner only stops callers fighting
  it. Figures that move, each on the kit list's Unit cost column and the
  item-detail "Supplied by kits" card:
  - A typed unit cost on a link that already had a package cost: the old price
    stayed. A link at `unit_cost 5.00 / package_cost 5.00 / pack 1`, operator
    types `7.00` -> stored **5.00** before, **7.00** now.
  - A first price on a link created with a blank cost box: the derived package
    price was dropped. Typing `4.00` stored `unit_cost 4.00 / package_cost
    NULL` before and stores `4.00 / 4.00` now, so the scan page stops saying
    "Package cost: — (no price on file)" for a package it can cost.
  - Clearing a recorded price: `null` was overwritten by the re-derivation and
    the price stayed. It now stores NULL and the item reads as unpriced.
  `inventory/tests/test_supplier_terms_single_owner.py` is the build gate, the
  write-side twin of the price gate. It surfaced four writers no hand sweep had
  named — all four turned out to write FLAGS rather than costs
  (`update_lead_times`, `mark_discontinued`, `void_line_item`,
  `enforce_single_primary`) and are allowlisted with that reason.
- **WHAT WAS ALREADY BROKEN, verified against base `7c078de` rather than
  assumed** — the captain asked for this explicitly, so each kit-form defect is
  labelled:
  - Blank cost box stored as `Decimal("0")` instead of NULL: **PRE-EXISTING.**
    Base's `KitDetailPage.handleSave` carries the identical
    `unitCost === '' ? '0'`. What this branch changed is VISIBILITY — the kit
    list and the "Supplied by kits" card used to render an em dash, so the
    fabrication was indistinguishable from "unpriced"; once they rendered
    `$0.00` it became a confident figure on screen.
  - `setdefault("quantity_per_package", 1)` resetting a recorded pack size and,
    through the derivation, the PRICE with it: **PRE-EXISTING.** Base carries
    that line verbatim.
  - A typed `unit_cost` inert on a link that already has a `package_cost`:
    **PRE-EXISTING.** Base carries the same partial `defaults` dict, and the
    re-derivation in `save()` predates the branch.
  - The derived `package_cost` dropped by `update_fields` on update:
    **PRE-EXISTING MECHANISM, NEWLY REACHABLE.** `update_or_create`'s
    restriction predates the branch, but the state that reaches it — a link with
    both costs NULL — is created by this branch's own blank-box fix, so the
    branch is what makes it occur.
- Supplier detail price-trend **records** (`GET /api/inventory/suppliers/<id>/`,
  `trends[].price_history[].unit_cost`) — `null` -> `0.0` for a recorded zero,
  which is what the supplier-detail chart plots.

**A reported figure that does NOT move, measured and rejected — the wire-type
rule cuts BOTH ways.** Review reported that a PO-form freeform line priced at
`0` passes `canSubmit` and is then silently dropped by
`.filter((item) => item.description && item.unit_cost)` and three sibling
readers, calling it operator input silently discarded. It is not, and the
reasoning inverted the very rule recorded above: `"0"` is a truthy STRING in
JavaScript — `Boolean("0") === true`, and only `""` is falsy. The form's
`unit_cost` is component state assigned straight from `e.target.value`, so it
is always a string; the falsy-at-zero trap needs a NUMBER, which is why it
bites the property-backed `InventoryItem.unit_cost` and not a text input.
Measured end to end: with a freeform line at `0`, the submitted `items` carry
`{description, quantity, unit_cost: 0}`, the Line Total cell reads `$0.00`
rather than the page's `—`, and the Additional Items subtotal counts it. No
guard was changed. The COVERAGE gap the report named was real, though — nothing
asserted the submitted payload — so `PurchaseOrderFormUnpricedLines.test.tsx`
gained CONTROLs for the freeform and asset halves that pin exactly this, and a
blank cost box still blocking submit.

One figure the change list over-claimed, corrected: the supplier detail's
price-trend **summary** (`average_unit_cost` / `min_unit_cost` /
`max_unit_cost`) is byte-identical to base, which already spelled that filter
`if ph.unit_cost is not None`. Nothing moved there. Both are pinned in
`test_price_guards.py`, the summary as a CONTROL and the records as the
BEFORE/AFTER.

**Two more deliberate exclusions, on the boundary the gate made visible.** Both
are the same falsy shape on a value this branch does NOT own, so repairing them
would move output for a reason that is not "base presented an unknown price as a
real number":

- `ReorderRequest.actual_cost` and `ReorderRequest.cost_per_unit`, and the
  truthiness on them in the TRANSPARENCY PAYLOAD specifically — both the
  `orders` block and the `ledger` block. `actual_cost` is a nullable column an
  operator types in, not a derived price, and this branch did not change it.
  `PurchaseOrder.actual_total` in the `purchase_orders` block is the same
  shape and excluded for the same reason: `null=True` and operator-typed. That
  is precisely what separates it from `estimated_total` beside it, which is
  non-nullable-with-default and therefore inside the branch — the nullability
  of the column, not the name of the field, is what decides.
  Read that narrowly: it does NOT extend to `PurchaseOrderItem.actual_cost`,
  which is DERIVED from `unit_cost_actual` and IS inside the branch (the
  `receiving.py` twin was the real defect), and whose two admin columns are
  named in the moved-figures list above.
- `MaintenanceItem.estimated_cost` on the work-order PDF
  (`inventory/utils/work_order_pdf.py`), where `if item.estimated_cost:` omits
  the "Est. Cost" line for a task budgeted at a recorded `0.00`. A maintenance
  budget, not a supplier price. REPORTED, NOT FIXED — allowlisted in the gate
  with that reason. Its sibling `or Decimal("0.00")` sites in
  `inventory/views.py`, `inventory/services/work_order_reports.py` and
  `analytics/services/aggregation.py` are INERT rather than excluded: the
  fallback IS `0.00`, so a recorded zero and a `NULL` produce the same number
  either way. `PurchaseOrder.effective_estimated_total`'s
  `self.estimated_total or Decimal("0.00")` is inert on the same argument, and
  doubly so because that column cannot be `NULL` at all.

**Not an exclusion — a correctness finding, and the distinction matters.** An
earlier round filed the truthiness on the scan screen's `supplier.unit_cost` as
a deliberate EXCLUSION, reasoning that a free vendor could never be preselected.
It was measured wrong and the entry was hollow, so it was deleted rather than
reworded. Truthiness on a STRING-valued price is not something this branch
tolerates; it is something the branch has verified is CORRECT, per the wire-type
table above, and changing it would be churn. Anything in this section is
excluded because the VALUE is outside the branch, never because a guard on it
merely looks safe. If you are about to file a frontend price guard either way,
measure the serializer field first — `type(SomeSerializer().fields["unit_cost"])`
answers it in one line.

"A price as JSON" has one owner too: `pricing.price_float`. It was written out
twice, character-for-character, in `inventory/views.py` and
`inventory/serializers.py` — two spellings of one fact on a branch whose whole
thesis is that there should be one. Non-functional; no figure moved.

**THE CROSS-PROJECT CONTRACT: two changes ScanTTY must make.** Verified against
`uid0/scantty` remote main at `385d12ae` — a fresh clone whose SHA was confirmed
through the GitHub API, not a local checkout — by probing its real Go structs
with the new payloads. Recorded here because this repo is what BREAKS them, and
because the defect they cause is the one this branch exists to close, displaced
one repository along:

- `PurchasingPriceTrend.MinUnitCost` / `MaxUnitCost` / `LatestUnitCost` are
  plain `float64`. The `null` `reorder_queue/views.py`'s `_as_float` now sends
  unmarshals to `0` and re-renders as "$0.00" — an unknown price presented as a
  fact. They should become `*float64`, as `PriceChangePercentage` on the same
  struct already is.
- `suggested_unit_cost` is now nullable (`services/line_entry.py`), so
  ScanTTY's `poAddIsZeroMoney(SuggestedUnitCost.String())` stops matching and
  its "there is NO price on file" hint disappears. It should become
  `SuggestedUnitCost.Empty()` — which additionally fixes that hint currently
  MISFIRING on a genuinely free vendor.

**Verified SAFE, so nobody redoes the work:** every other field this branch
made nullable is a `DecimalString`, which already handles `null` as `Empty()`;
the new payload keys (`unit_cost_state`, `unit_cost_detail`,
`unpriced_item_count`, `estimated_total_is_partial`, `items_without_price`,
`direction`) are ignored by `encoding/json`; `create_optimized_order` is not
among the endpoints ScanTTY calls at all; and both new write-path refusals
reach an operator with the full remedy text.

### The pre-send boundary: when a PO is still the shop's own document

`PurchaseOrder.PRE_SUPPLIER_STATUSES` is the ONE definition of "the supplier has
not seen this order", and it sits beside `RECEIVABLE_STATUSES` /
`IN_RECEIVING_STATUSES` on the model. Both line-set guards read it —
`services.line_entry.assert_addable` and `assert_deletable` — and the API serves
the answer rather than making clients derive it: `can_delete_items` on the order
serializer (beside `can_receive`, same discipline) and `can_add_items` on the
item-lookup payload. Gate on the set; never compare to `Status.DRAFT` by name,
or a second pre-send state becomes a hunt through the comparisons.

It is what separates the two line-removal verbs, which are NOT variants of each
other: while the order is pre-send a mistaken line is a typo and is DELETED
outright (no reason, irreversible, no ghost); once the supplier holds a copy it
can only be VOIDED (reason required, struck off, kept on the record). The web
page offers exactly one of the two, chosen off `can_delete_items`.

The delete path REFUSES a line carrying `quantity_received > 0`, with a 400 and
a message naming the recorded quantity. `DRAFT` is initial-only — nothing in
the codebase writes an order back to it — so that branch should be unreachable,
and it is kept anyway: an impossibility argument is only true while every
future change re-verifies it, whereas a guard holds without anyone re-verifying
anything, and what it protects against is destroying goods a receipt says
arrived. Prefer the guard to the argument wherever the argument is about what
some other part of the codebase will never do.

Outside the pre-send set sit two different reasons, and `assert_deletable` says
whichever is true: "the supplier already has this line, void it instead" and
"this order is closed and never went out, start a new one". The split is derived
from the two frozensets — the closed case is *outside `PRE_SUPPLIER_STATUSES`
and outside `IN_RECEIVING_STATUSES`*, i.e. the terminal statuses, never a typed
list of labels — with `sent_at` read only as corroboration. Do NOT key such a
split on `sent_at` alone: `PurchaseOrderAdmin.mark_as_sent` moves a queryset to
`SENT` with one `update()` and stamps `sent_by` but not `sent_at`, so an order
can be live with its supplier and hold no stamp. A refusal is only legitimate
when the operator can act on it, and a refusal that misstates why is worse than
a bare one.

One thing found here and deliberately NOT changed, routed to follow-up instead
(the list-filter defect found alongside it — `get_queryset` dropping a draft
off the list once its only line was gone — has since been fixed, and the next
section owns that rule):

- `PurchaseOrderAdmin.mark_as_sent` never stamps `sent_at`, which also leaves
  `days_since_ordered` reading 0 for orders sent that way. Changing it alters
  existing admin behaviour, so instead nothing in this change depends on that
  stamp being written.

### Two kinds of empty purchase order, and when emptiness hides one

The LIST action hides an order **emptied by voiding** that is outside
`PurchaseOrder.PRE_SUPPLIER_STATUSES`, and nothing else. An order with **no
line items at all** is listed in every status.

No status NAME appears in the condition: the pre-send clause reads the
`PRE_SUPPLIER_STATUSES` frozenset, the same one `assert_addable` /
`assert_deletable` / `get_can_delete_items` read, so a second pre-send status
is one edit to that frozenset and none of it is here.

`PurchaseOrderViewSet.get_queryset` owns the derivation;
`reorder_queue/tests/test_po_list_emptiness.py` owns the behaviour, crossing
status × line-population rather than enumerating cases in prose.

Two related questions are known and filed separately: the `VOIDED`-order
display inconsistency on staff's list, and `void_item` carrying no status gate.

### Purchase-order line settlement

"Is receiving finished with this line?" is defined once, on
`PurchaseOrderItem.is_settled`, and nowhere else. Six defects had come from
code answering it with a predicate of its own — the last one from another app
entirely — so `backend/reorder_queue/settlement_sites.py` derives the whole set
of sites from that property (it walks it with `ast` to the model fields, then
sweeps `backend/` and `frontend/src`) and fails when one bypasses it. Run it for
the report, `--sites` for every reader:

```
python3 backend/reorder_queue/settlement_sites.py
```

It runs as `reorder_queue/tests/test_settlement_sites.py` in Backend Tests and
as a step in Frontend Lint, so a frontend-only PR is covered too. If it flags
your change, route the site through the derivation rather than widening the
guard: `PurchaseOrderItem.receipt_state` / `is_settled` in Python,
`PurchaseOrderItem.q_settled()` / `objects.outstanding()` /
`objects.with_receipt_state()` in the ORM, and `receipt_state` / `is_settled`
off the API on the frontend. Anything that can settle a line must reach
`services.refresh_receipt_status` before it returns.

**Where the refresh actually lives.** Saving or deleting a LINE re-derives its
order on its own — `reorder_queue/settlement_signals.py` hangs off
`PurchaseOrderItem`'s `post_save` / `post_delete`, and `pre_save` captures the
order a line is LEAVING so a reparent re-derives both ends. No admin hook owns
that re-derivation any more — the admin still opens `settlement_batch()` so a
formset save asks once, and `ReceiptStatusFilter` still *reads* settlement
through `with_receipt_state()`, but neither decides it. A hook used to: the
change form, then the inline formset, then row delete, then bulk delete, then
reparenting, each closed by adding another method name to a list, which is the
mistake this section exists to stop.

**The delete signal carries a second, non-settlement obligation.** A line
DELETE also re-rolls `PurchaseOrder.estimated_total`, which is a STORED sum
frozen from the line costs — voided lines stay in it (`effective_estimated_total`
subtracts them at read time), but a deleted line is subtracted by nobody, so
without this the order reports money for a line that no longer exists. It rides
`post_delete` rather than living in the delete endpoint because the Django
admin's row / inline / bulk deletes are three more routes that remove a line,
and they were already overstating the total before that endpoint existed.

The rule is "a line's cost LEFT the order", so it covers the admin change
form's REPARENT too — moving a line to another order removes its cost from the
one it left exactly as a delete does, and leaves the one it joined
understating. That case re-rolls both orders from the post_save receiver.

Ordinary line SAVES stay excluded, and the boundary is narrower than it sounds:
the API's own `add_line_item` / `update_item` re-roll on their own path, but the
admin does NOT — neither `save_model` nor `save_formset` calls
`recalculate_estimated_total` — so an admin quantity edit, reprice or inline add
still leaves the stored total stale. It is left open deliberately, because the
signal only compares fields inside the settlement closure and
`unit_cost_ordered` is not one of them, so closing half of it would mean an
invariant documented as held and not held. `oms-derived-totals-beyond-settlement`
(order-level figures computed from lines that only some line-writing paths
re-derive) is STILL NEEDED for the rest; removal is covered, editing is not.

When you state a rule like this one in a docstring, read it back against every
route that satisfies its antecedent — the reparent gap was a stated rule the
code did not honour, and it is the worked instance to build that issue on.

**What the signal does NOT cover, and why the guard still has a job:**
querysets fire no per-object save signal. `PurchaseOrderItem.objects.filter(...)
.update(...)` and `bulk_update` write settlement columns with nothing hearing
about it, so those paths must call `services.refresh_receipt_status` themselves
— `services.purchase_orders.void_po` is the live example. Ordinary
`queryset.delete()` IS covered (it fans `post_delete` out per row), but a FAST
DELETE is not: a collector that can drop rows with one `_raw_delete` sends no
signal, and `_raw_delete` called directly never does.

Three properties the routing holds, all pinned by tests rather than asserted:
receiving a twenty-line order re-derives the order ONCE (`settlement_batch()`
coalesces inside the caller's unit of work, never on `transaction.on_commit` —
endpoints serialize `purchase_order.status` into the response and ScanTTY reads
it); a save that moved no settlement field and did not move the line to another
order re-derives NOTHING, so editing a note leaves an operator's chosen status
alone; and a refresh cannot re-enter its own signal.

Do not read a clean run as "there is nothing left". The scan prints the write
shapes it can and cannot see on every run; that list is the honest boundary and
it has grown twice already.

**A file it could not read is not a file it cleared.** The scan exits non-zero
for a module it cannot parse or decode, not just for a site that bypasses the
derivation, and names each one under `NOT scanned`. It used to `continue` past
them, so a run in which N modules failed to parse still printed `Scanned:
backend, frontend/src` and a clean verdict — which is why Frontend Lint now sets
up Python 3.14 before running it. Run the scan under the version the backend
targets; a partial sweep says so in its summary rather than passing for a whole
one.

**A custom `QuerySet` method can become a `Manager` method by accident.**
`BaseManager._get_queryset_methods` copies every public queryset method onto the
manager EXCEPT those marked `queryset_only`, and that marker does not survive an
override. `PurchaseOrderItemQuerySet.delete` shipped without re-setting it,
which made `PurchaseOrderItem.objects.delete()` a real, callable method that
takes no filter and empties the table. Any override of a method Django withholds
needs `<name>.queryset_only = True` after its `def`, the way `QuerySet`'s own
does. Enforced across every model in the repo — the check derives the withheld
set from `QuerySet` and `Manager` rather than naming `delete`, so a second
queryset class in any app is already covered.

### Django upgrade history

The backend now runs **Django 6.0.7**. The notes below cover the earlier 4.2 -> 5.1
upgrade and are retained for context; anything they say about 5.1 being the
current version is superseded by the 6.0.7 pin in `backend/requirements.txt`.

Django 6 changes that affect code in this repo:

- `CheckConstraint(check=...)` is gone -- use `condition=`.
- Constraint validation runs in `Model.full_clean()` by default; pass
  `validate_constraints=False` where a model deliberately leaves enforcement to
  the database (see `inventory/models/kit.py`).

The project was successfully upgraded from Django 4.2.27 to Django 5.1.14, then to 5.1.15 for security. Key points:

- **All tests pass**: 389 passed, 2 skipped with Django 5.1.15
- **No breaking changes**: All custom admin filters (`DeliveryPerformanceFilter`, `ReceiptStatusFilter` in `reorder_queue/admin.py`) work correctly
- **Package compatibility**:
  - `django-passkey-auth==0.2.0` works with Django 5.1 (no explicit support but tested and functional)
  - All third-party packages updated to Django 5.1-compatible versions
- **No deprecation warnings**: Clean upgrade with no deprecated features in use
- **Database**: PostgreSQL 15 meets Django 5.1 requirements (13+)
- **Migrations**: All migrations run cleanly, no issues detected

Updated packages:

- Django: 4.2.27 → 5.1.14 → 5.1.15 (security fix for XML deserialization DoS vulnerability)
- djangorestframework: 3.15.2 → 3.16.1
- django-cors-headers: 4.6.0 → 4.9.0
- django-redis: 5.4.0 → 6.0.0
- django-celery-results: 2.5.1 → 2.6.0
- drf-spectacular: 0.27.2 → 0.29.0

## Frontend

- Use functional components with hooks
- Follow a consistent folder structure (components, screens, navigation, services, hooks, utils)
- Use React Navigation for screen navigation
- Use StyleSheet for styling instead of inline styles
- Use FlatList for rendering lists instead of map + ScrollView
- Use custom hooks for reusable logic
- Implement proper error boundaries and loading states
- Optimize images and assets for mobile performance
- **A rejected field never reaches the operator through `extractErrorMessage`**: the
  error envelope ([`docs/API_ERROR_CONTRACT.md`](docs/API_ERROR_CONTRACT.md) owns its
  shape) keeps the field map in `error.details` behind a flat "One or more fields failed
  validation." message, and `src/utils/extractErrorMessage.ts` returns only that message.
  A form that has to name the rejected field reads `details` itself —
  `src/utils/supplierRelationships.ts` is the pattern.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
