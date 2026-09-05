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

The forecasts ARE on this derivation as of op-3vqk, but as a PREFERENCE rather
than a filter, and the difference is the whole history of this line.
`component_forecast.lead_times_for` is the single lead-time resolver for both the
serialized report and the nightly demand forecast, and it asks `select_suppliers_for`
first: the chosen link's `LeadTimeLog` mean, else that link's `average_lead_time`.
Only when the derivation answers `NONE_ORDERABLE` does it fall back to reading
EVERY link — the pre-op-3vqk expression, verbatim, for exactly the population
that still needs it. A plain filter here is what op-2rsp round 5 shipped: an item
whose only vendor is discontinued then has no lead time at all, its threshold
collapses to zero days, and it silently leaves the demand-forecast report AND the
nightly digest. `inventory/tests/test_alert_suppression.py` still pins the
fallback with `test_the_serialized_forecast_keeps_a_dead_vendors_lead_time` and
`test_an_item_whose_only_supplier_died_reaches_the_report_and_the_digest`, and
`inventory/tests/test_forecast_lead_time_source.py` pins the preference; see "The
alert-suppression class" below.

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
   rank the candidates on cost, lead time and delivery record.

Ask `select_supplier` / `select_suppliers_for` when you must explain yourself to
an operator: they separate `NO_SUPPLIERS` from `NONE_ORDERABLE`, which are
different facts needing different actions, and flag when the operator's own
choice was the row that got skipped. `primary_item_supplier` is the same answer
with the reason dropped. The memo lives on `InventoryItem.supplier_choice` (the
whole `SupplierChoice`), and `primary_item_supplier` reads through it, so asking
for the reason as well as the row is ONE resolution.

**A surface that NAMES a supplier reads `supplier_choice`, never the flat
`supplier_name` (op-3xsp).** The flat key is the same winner with the derivation
thrown away: it cannot say what else was on offer, that the scoring knew no price
for this one, or that the operator's flagged primary was skipped — which is how
an item with three sources came to render as an item with one on the scan page,
the reorder queue, the item page and the CSV export that leaves the system and
gets ordered from.

The boundary is NAMING a supplier, not showing a number that came from one, so a
price column attributes nothing and is governed by op-9m2v instead. A SKU is the
one exception, and not a close call: a part number gets PASTED INTO A VENDOR'S
ORDER FORM, so an unattributed one is actionable-wrong in a way an unattributed
price is not. Reading the same key is not always the same question either — a
form that EDITS one relationship, a report grouped by the supplier an order
ACTUALLY went to, and a scan that matched one vendor's barcode are legitimately
singular and are NOT on this rule.

The seven flat compat fields stay. The `InventoryItemSerializer` comment names
each remaining reader with the surface it lives on and is the record of who
still needs them; check it there rather than restating it here. The web words
the whole choice in ONE place, `frontend/src/utils/supplierChoice.ts`. What any
of it exposes to an unauthenticated caller is in
`docs/API_PERMISSION_MATRIX.md`.

Filtering happens in Python, on `item_suppliers.all()`, so the prefetch cache
still serves it — a fresh `.filter()` reintroduces the per-row N+1 that #882
removed and that `docs/API_LIST_CONTRACT.md` bounds in CI. The cost yardstick
(`average_orderable_unit_cost`) is computed in Python for the same reason.

**Prefetch through `supplier_selection.item_suppliers_prefetch()`, never the bare
`"item_suppliers__supplier"` string.** It pulls the same rows and adds the two
delivery-record annotations the performance term reads, in the same round-trip.
A queryset that prefetches the bare string still gets the right answer, but pays
one extra grouped aggregate per page for it.

**The scoring weights are a product decision, not an implementation detail**, and
`inventory/tests/test_supplier_scoring.py` is where each one is asserted on
real-shaped catalogue data. Its module docstring is the current record of what
was decided and what is still open; read it before touching a weight. The shape
of the arithmetic is one sentence: **every term starts at its full weight and is
discounted only by evidence against that candidate**, so a gap in the data — no
price on file, no delivery ever recorded — is neither punished nor paid, and the
winner reports the gap (`SupplierChoice.scored_without_price` /
`scored_without_history`, reaching `/items/{id}/metrics/` as
`supplier_scored_without_price` / `supplier_scored_without_history`) rather than
leaving an operator to infer it from a blank cell. One judgement is still
`REPORTED, NOT FIXED` there: where the cost cliff sits (150% of the item's
average). Retuning it needs a captain decision, and the tests fail until it is
deliberate.

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

### Which supplier's wait the reorder point allows for (op-3vqk)

`component_forecast.lead_times_for` is the ONE lead-time resolver, feeding the
serialized forecast's `reorder_point` and — through `inventory.tasks
.generate_demand_forecasts` — the stored demand forecast's `needs_reorder`
threshold and so the nightly digest. The rule, in one sentence: **the reorder
point must be computed from the lead time of the supplier we would actually buy
from; and an item with no orderable supplier must still appear on the forecast,
with its lead time honestly attributed.**

Before this, two resolutions ignored who we buy from: the observed mean averaged
`LeadTimeLog` across ALL of an item's links, and the estimated fallback took the
flagged-primary link's `average_lead_time` — or, with nothing flagged, whichever
row the planner returned first, since the query ordered by `-is_primary` and
nothing else. An item with a flagged 30-day primary could therefore be costed at
a 7-day rival's wait, understating its reorder point roughly fourfold and sitting
below its true trigger unflagged; and the same shape could resolve two ways in
one request.

It returns a `LeadTime(days, basis)`, and the basis is three-valued because these
are three facts, not two:

- `orderable_supplier` — the link `select_suppliers_for` picked. The only basis on
  which `reorder_point` is a horizon anyone can order against.
- `unorderable_supplier` — links exist, every one inactive or discontinued. The
  number is REAL and the row keeps its full lead component and its flag; the
  vendor behind it just cannot be bought from.
- `no_supplier` — no link, so nothing on record. The only basis where
  `lead_time_known` is false.

**One alert clears, and it is a STATED EXCEPTION to the "nothing leaves"
invariant — do not "fix" it back.** Correcting whose wait the threshold uses
moves flags in BOTH directions, and one direction removes an alert. The shape:
a live 30-day link beside a discontinued one that once took 60 days. The old
rule averaged `LeadTimeLog` across every link, so the 60 became the threshold
and the item was flagged 38 days out; we would actually buy from the 30-day
link, so it is not yet due within its lead time. It keeps its row on the
unfiltered demand-forecast report throughout, with the SAME predicted due date
— only `lead_time_days` (60 → 30) and `needs_reorder` move — and it returns to
`demand_forecast?low_stock_only=true`, `reorder_alerts` and the nightly digest
**eight days later**, when the due date comes inside the buyable supplier's
wait. Nothing is lost, only deferred to the date the real vendor makes true.
The alternative — flagging on the maximum of the buyable and the on-record wait
— puts a dead vendor's number back into a live vendor's threshold, which is the
defect this section exists to remove. Captain's call, taken deliberately.

Note the vocabulary trap while reading the invariant: `test_an_item_whose_only_supplier_died_reaches_the_report_and_the_digest`
uses "the report" to mean `latest_demand_forecasts(low_stock_only=True)`, the
FILTERED view — not the unfiltered endpoint. Say which you mean; the two give
opposite answers about whether an item "left".

**Where the operator can see the difference, and where they deliberately cannot.**
`lead_time_basis` is on the serialized-forecast payload and
`SerializedForecastPanel` words all three in the reorder-point cell: a plain
number, `N *` with a tooltip saying the wait belongs to a discontinued or
inactive supplier and that a live one has to be found, and `≥ N` for the lower
bound. It is **not** on `DemandForecast` — that would be a migration for a
value no surface reads, and the stored row already carries the `lead_time_days`
its threshold used. So on the demand-forecast report and in the nightly digest an
unbuyable item's wait is NOT distinguishable from a live one's. Do not write
anywhere that it is; adding the column and a reader is filed as follow-up
`oms-demand-forecast-lead-basis`.

### A lead-time lateness must name the promise it scores

`LeadTimeLog` carries TWO promises and only one is scored. `variance_days`,
`was_late` and every rate derived from them measure the supplier link's STANDING
QUOTE; `expected_delivery_date` is the separately confirmed order date and
nothing scores it. That is deliberate — the model docstring carries the
reasoning, and `test_variance_scores_the_standing_quote_not_the_confirmed_date`
pins it. Do not reopen it.

The consequence for anything you build: a row can read `+7, was_late` having
arrived on the day the operator agreed, so **no screen, payload or export may
show one of those numbers without naming the yardstick**, and per-row surfaces
show `met_confirmed_date` beside it. Take the words from
`LeadTimeLog.VARIANCE_YARDSTICK{,_LABEL}` rather than writing your own, and note
`met_confirmed_date` is tri-state — `None` where the order confirmed no date,
because this row's own `expected_delivery_date` falls back to the quote and is
then not an agreed date at all. `reorder_queue/tests/test_lead_time_yardstick_is_named.py`
pins every surface and fails if a new one renders a bare "N days late".

Naming the yardstick means naming it in the KEY, not only in the label a person
reads: a consumer decoding `on_time_rate` or `was_late` asserts a bare lateness
however the screen is worded. Two contracts were therefore RENAMED, which is a
breaking change for any client outside this repo:

`GET /api/inventory/suppliers/<id>/` and `/analytics/`, in the
`lead_time_analytics` block served identically by both:

| was | is |
| --- | --- |
| `on_time_percentage` | `within_quoted_lead_time_pct` |
| `average_variance` | `avg_variance_vs_quoted_lead_time_days` |
| `recent_logs[].was_late` | `was_over_quoted_lead_time` |

`GET /api/reorders/reports/purchasing/export/?type=lead_time_analysis`, in the
CSV header row (machine keys, matching the export's three untouched siblings) —
any spreadsheet keyed on the old header must be re-pointed:

| was | is |
| --- | --- |
| `avg_estimated_lead_time` | `avg_quoted_lead_time_days` |
| `avg_variance` | `avg_variance_vs_quoted_lead_time_days` |
| `on_time_rate` | `within_quoted_lead_time_pct` |

One concept, one name across both. What bounds the rename is the cross-project
contract, not taste: ScanTTY decodes `on_time_delivery_rate`,
`late_delivery_rate`, `early_delivery_rate`, `average_variance_days` and the
`lead_time_analysis` JSON's `avg_variance` / `on_time_rate` BY NAME off
`/api/reorders/analytics/` and `/api/reorders/reports/purchasing/`. Those keys
were deliberately NOT renamed and must not be; they carry
`variance_measured_against` alongside instead. ScanTTY decodes nothing off the
supplier endpoints and does not read the CSV, which is what made those two
renameable.

A rate or average of exactly `0` is an ANSWER, not an absence — a vendor that
hit its quote on every order averages a variance of `0.0`, and a counter-pickup
supplier averages a `0`-day lead time. Guard these with `is not None`, never
truthiness, or the payload reports a perfect record as "N/A" beside a sibling
card reading 100%. See also the alert-suppression class below.

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
everywhere. Do NOT FILTER the lead-time resolver to orderable links —
`test_the_serialized_forecast_keeps_a_dead_vendors_lead_time` and
`test_an_item_whose_only_supplier_died_reaches_the_report_and_the_digest` in
`inventory/tests/test_alert_suppression.py` pin that, and both fail if the filter
comes back. **Preferring** the orderable link and falling back to every link is a
different shape and is what the resolver does now; see the section above. That
function's own docstring carries the rest of the reasoning; read it before
changing anything there.

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
`oms-falsy-zero-money-guards`. See "What a price costs" below. The supplier
scoring's own falsy guards — a `unit_cost` of 0 and an `average_lead_time` of 0
both read as "unknown" — were reserved to the captain and are now CLOSED too
(`oms-supplier-scoring-weight-flaws`; `test_supplier_scoring.py` pins the new
behaviour, and `receiving.create_lead_time_log`'s `average_lead_time or 14`, the
guard that wrote a fortnight into a same-day vendor's delivery record, went with
them). `get_expected_delivery_date`'s
`and self.average_lead_time` — where a KNOWN zero-day lead time yields no date —
was filed here as "the same shape", and it is: but it moves a DATE, not a money
figure, so it was outside the money branch's invariant and is STILL OPEN.

Three more, found by this branch's sweeps and deliberately NOT fixed here. The
first has since been CLOSED (`oms-scan-autosubmit-units-and-retry`); 2 and 3 are
still open:

1. **`ScanPage`'s anonymous auto-submit misdescribed a KNOWN case size — CLOSED.**
   It printed "3 cases" and posted the raw `reorder_quantity`, which for a
   pack-counting item is a count of PACKS, so it filed a twelfth of what it
   named. The durable rule:

   > A `ReorderRequest`/`PurchaseOrderItem` quantity is ALWAYS base units.
   > `minimum_stock` and `reorder_quantity` are NOT: for the pack-counting
   > `count_mode`s they are amounts in the item's own count unit.

   A surface that FILES a reorder therefore reads
   `reorder_display.order_quantity` — the wire face of
   `packaging.base_reorder_quantity`, which also fills a purchase-order pad —
   and prints its `order_text`. `reorderQuantityLabel` answers the other
   question (the CONFIGURED amount, in the item's counting unit) and is right
   wherever the reader is not being promised what will be filed. Pinned by
   `inventory/tests/test_reorder_filing.py` and `ScanPage.test.tsx`.

   ⚠️ **One part is still open and is the captain's.** For a legacy
   `use_case_based_reorder` item with no packaging chain of its own,
   `reorder_cases` sizes only presentation and reaches no ordering path, while
   `reorder_quantity` sizes what is ordered — so "Reorder Cases: 4" never
   reaches the order. Closing that changes what is ordered for live items: **do
   not close it without that decision.**
   `TestLegacyCaseBasedItemsAreRecordedAsTheyBehave` pins it, including the
   bridged shape, where `counts_in_packs` wins and both halves read
   `reorder_quantity` instead.

   Derived set, exclusions, retry-bound reasoning and the ScanTTY check:
   [`docs/oms-scan-autosubmit-units-and-retry-record.md`](docs/oms-scan-autosubmit-units-and-retry-record.md).
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
- **The supplier scoring's half of this is now CLOSED.**
  `score_candidate`'s `if link.unit_cost and average_unit_cost` was the same
  mistake — a free supplier could never win on price while its `0.00` still
  dragged the yardstick. It was captain-reserved, because repairing it changes
  which supplier the system picks, and the captain has since decided it: the
  cost term reads through `pricing.unit_price_of`, so a `0.00` is the known
  price it is, and `test_a_free_supplier_is_priced_at_zero_and_wins_on_it` pins
  the outcome. Shipped as `oms-supplier-scoring-weight-flaws` — see the
  supplier-selection note in the falsy-zero section above for the rest of what
  that branch settled.

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

**Every money figure this branch moved is named individually, with the screen or
payload that shows it, in
[`docs/oms-falsy-zero-money-guards-record.md`](docs/oms-falsy-zero-money-guards-record.md).**
That list is the branch's evidence, not standing guidance; do not copy it back
here.

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
  named in the branch record's change list.
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

**DECIDED AND DONE — the public inventory-summary valuation.** Filed by
`fm/oms-falsy-zero-money-guards` as an escalation; the captain chose to stop
publishing the valuation anonymously rather than correct the matrix, and
`fm/oms-public-inventory-valuation` implemented it. `get_inventory_summary`
stays `AllowAny` — a function-based view has no `get_permissions` seam, so the
gate is on the FIELD: `total_value` and `items_without_price` are absent for an
anonymous caller and replaced by `"total_value_withheld": true`; an
authenticated caller's payload is unchanged. Absent rather than `null`, because
`null` already means "no price on file" in this payload family and a consumer's
`?? 0` would render the withheld figure as a real 0.00. The contract and its
rationale live in `docs/API_PERMISSION_MATRIX.md`'s
`dashboard/inventory-summary/` row and the view's own docstring.

**STILL OPEN, and deliberately untouched — the rest of the anonymous money
surface.** In production `REST_FRAMEWORK.DEFAULT_PERMISSION_CLASSES` is
`IsAuthenticatedOrReadOnly` (`config/settings.py`), so every viewset that does
not override it serves anonymous GETs. Purchase orders, item and supplier
costs, price history, asset purchase prices and maintenance estimates all reach
a caller with no session; `reorders/analytics/transparency/` does so by design.
The full derivation is in the `fm/oms-public-inventory-valuation` PR body.
Queued as `oms-anonymous-read-posture` — a captain decision, not an
implementation one. Do not narrow any of it without that decision.

**FILED, APPROVED, NOT DONE — `log_usage`'s "no unit cost" warning. Recorded so
the branch's "the derived set AND its deliberate exclusions both reported with
reasons" criterion is not read as complete: this is a known gap in it.**
`inventory/views.py` `log_usage` gates the ledger posting at :1275 on
`total_cost is not None and total_cost > 0` and, when it does not post, returns
the warning at :1287, "committee recorded, but the item has no unit cost". A
DONATED item's `unit_cost` is now a real `Decimal("0.00")`, so `total_cost` is
`0` and the operator is told the price is UNKNOWN when it is known to be
NOTHING — the second half of the rule sentence, inverted onto a message. NO
MONEY MOVES and none should: skipping a zero-amount posting is correct, and
`receiving.py`'s own comment says why (a zero-amount transaction is ledger
noise, not a record of a payment). The fix is the WORDING only — split the
`else` on `total_cost is None` (unknown: keep a "no price on file" warning
naming the remedy) versus `total_cost == 0` (known zero: no price warning at
all) — plus a BEFORE/AFTER test for the donated case, a CONTROL for the
genuinely unpriced one, and a change-list entry. Three doc sites follow the
string and must move with it: the `log_usage` docstring (~:1199),
`docs/accounting.md` where it is quoted verbatim, and
`backend/inventory/tests/test_log_usage_charge.py`, which asserts it.
APPROVED BY THE OPERATOR and deferred only because the phase that found it
could not make functional changes — NOT declined.

### oms-supplier-terms-write-path — the DERIVATION is closed; some surfaces are not

**The derivation is closed and has one owner. The kit form's supplier-terms
surface is NOT.** A fix for it was attempted on this branch and SPLIT OUT after
three rounds that each produced the next round's defect. Its window — naming one
supplier while the boxes hold another's figures — is filed, with the trace and
with the reason the next attempt must not begin by seeding the supplier id, in
[`docs/oms-supplier-cost-write-path-record.md`](docs/oms-supplier-cost-write-path-record.md)
under "Still open, filed not fixed". That list also holds the lost-update window
on `stored_pricing` and the `supplier_terms` `DictField` 500s. **Read it before
touching the kit form**: "closed" below is about the derivation, not about every
screen that reaches it.

**One rule, in one place: `inventory.services.suppliers.derive_costs`, called
from `ItemSupplier.save()`.** `unit_cost` and `package_cost` are derived from
each other and `package -> unit` is LOSSY at two decimal places — `10.00 / 3`
stores as `3.33`, and `3.33 * 3` is `9.99` — so any write that handed the model a
partial picture made it re-derive the twin from a value nobody edited, and a cent
escaped on a save that touched no price.

**Intent is a DELTA against the stored row, never a rule about which keys a
caller sent.** That distinction is the whole bead. A form that echoes an
unchanged cost box and a form that omits it are indistinguishable by key, which
is why three successive caller-side rules each fixed one case by reopening
another and the attempt was withdrawn in full — the history is in
[`docs/oms-falsy-zero-money-guards-record.md`](docs/oms-falsy-zero-money-guards-record.md).
`save()` is the one place that sees BOTH what the caller supplied and what is
stored, so it is the one place the rule can be stated. **Do not re-open this at
the write sites.** A partial `defaults` dict is now safe by construction, so a
new writer needs no rule of its own.

The rule, decided by the operator and pinned in
`backend/inventory/tests/test_supplier_cost_derivation.py`:

- nothing moved — derive nothing; both stored prices stay byte-identical. A SKU
  or flag edit is not a price edit.
- `package_cost` moved — it governs. It is what the shop actually pays, and it is
  the only safe direction: `package -> unit` is the lossy half.
- `package_cost` cleared — both clear. "No price on file" has to stay sayable.
- `package_cost` cleared AND a `unit_cost` that MOVED against the stored row in
  the same request — the changed value wins and the case price re-derives from
  it. A value that MOVED beats a clear, whichever box it came from: taking the
  clears first discarded a freshly typed unit price without a word, and that is a
  regression this branch caught in its own first cut. An ECHOED unit cost does
  not qualify, and must not be read as one — stored (`package_cost` 10.00,
  `unit_cost` 3.33, pack 3), the operator empties the Package Cost box, and the
  form sends every offered field, so the request carries an unchanged `unit_cost`
  of 3.33. Nothing moved in that box, so the bullet above governs and both
  columns clear.
- only `unit_cost` moved — it governs; the case price re-derives. This is the
  ordinary case: every form sends both boxes and the operator edits one.
- only `unit_cost` cleared — it re-derives, because it is a derived figure. The
  write response carries the value it came back as, and the item form's
  relationship editor labels both of its boxes with the rule. The KIT form's box
  does NOT yet, so ruling (C)'s presentation condition is met on one surface and
  not the other; the gap is filed in
  [`docs/oms-supplier-cost-write-path-record.md`](docs/oms-supplier-cost-write-path-record.md)
  under "Still open, filed not fixed". Read that list before assuming the
  condition holds everywhere.
- only the pack size moved — hold `package_cost`. "The case holds 6, not 3" is
  about packing, not about price.

Two consequences worth keeping in mind when touching this path:

- **A derived column is added to `update_fields`.** `QuerySet.update_or_create`
  restricts `update_fields` to its own `defaults` keys, so without that a
  `package_cost` the model derives is computed and then dropped on the floor.
- **Test it at a pack size that does not divide the case price evenly.** Every
  pre-existing test of this behaviour used `quantity_per_package=1`, where the
  derivation is exact and NO defect on this path is reachable. That is how five
  symptoms reached main under a green suite.

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
