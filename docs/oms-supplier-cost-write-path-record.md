# `oms-supplier-cost-write-path` — the branch record

Closes the supplier cost write path that `oms-falsy-zero-money-guards` opened,
attempted and withdrew. Read
[`oms-falsy-zero-money-guards-record.md`](oms-falsy-zero-money-guards-record.md)
for that attempt; this file records what was done instead and why it is a
different shape.

## Why the last attempt failed

The withdrawn attempt is commit `78323d42` on `fm/oms-falsy-zero-money-guards`
(PR #1037). It changed `_apply()` in the single-owner module
`inventory/services/suppliers.py` from

```python
if len(supplied) == 1 and terms[supplied[0]] != getattr(link, supplied[0]):
```

to

```python
if len(supplied) == 1:
```

— rule 3, "key presence alone" — and moved value-equality out to a whole-request
`_is_echo()` early return.

**What it closed.** Rule 2's defect: a cost the caller submitted unchanged was
discarded, so restating `unit_cost` as `5.00` beside a new pack size was ignored
while `5.01` in the same request was honoured. Behaviour turned on a single cent.

**What it broke.** The value comparison had been added by `f91d8c7e` (round 12);
removing it in round 16 reopened that round's defect from the other side. A
SKU-only edit is not a whole-request echo, so `_is_echo` did not fire, `_apply`
saw the echoed `unit_cost` as "exactly one cost supplied", cleared
`package_cost`, and `save()` re-derived it — symptom 5, `10.00` becoming `9.99`.
The owner was reverted in full two commits later (`696500f6`, `6c2adc10`).

**The arithmetic.** `package_cost 10.00 / quantity_per_package 3` is
`3.3333…`, stored at `decimal_places=2` as `3.33`. Multiplying back:
`3.33 × 3 = 9.99`. The map `package → unit` is many-to-one — `10.00`, `9.99` and
`9.98` all reach `3.33` at pack 3 — so `unit → package` cannot recover which case
price was meant. The derivation does not round-trip, and it can gain as well as
lose: at pack 7, `10.00 → 1.43 → 10.01`.

## The shape of the fix

**Not a fourth rule at the callers.** A form that echoes an unchanged cost box
and a form that omits it are indistinguishable by key, which is what made the
first three rules unwinnable. `ItemSupplier.save()` can read the stored row —
`pricing_changed()` already did — so intent is a **delta** against what is
stored, which is well defined however the caller phrased itself.

One rule, `inventory.services.suppliers.derive_costs`, called from `save()`.
`AGENTS.md` had already recorded this as the required direction: "the next
attempt should address the derivation in `save()` itself rather than the
callers".

## What the operator decided, and what it changed

| | ruling | measured against base |
|---|---|---|
| (A) both costs submitted and disagreeing | `package_cost` governs | **already base behaviour** — confirmed, now pinned |
| (B) only the pack size moved | hold `package_cost`, re-derive `unit_cost` | **already base behaviour** — confirmed, now pinned |
| (C) clearing one of the pair | clearing `package_cost` clears both; clearing `unit_cost` re-derives it, **observably** | changed; the observability half is new |

(A) and (B) required no behaviour change. They were unasserted, which is exactly
what let a later round undo one — so they ship as CONTROLs whose job is to make
that undoable-ness fail.

## The set: where does this system write a supplier price?

A supplier price is `ItemSupplier.unit_cost` / `package_cost`, plus
`quantity_per_package`, which participates in both directions — and the
`PriceHistory` rows that record them. Every writer reaches `ItemSupplier.save()`,
which is why the fix is there and not at any of them.

| # | site | what it is | disposition |
|---|---|---|---|
| 1 | `ItemSupplier.save()` | the derivation | **the root** — one rule, `derive_costs` |
| 2 | `views.InventoryItemViewSet._create_supplier_relationship` | item CREATE only; its update branch is unreachable (`_sync_primary_supplier` is called from `create()` alone, and `suppliers` is `read_only` on the item serializer) | omits an absent cost instead of sending `None` |
| 3 | `serializers.KitSerializer._apply_supplier_terms` | kit create + update | stopped fabricating a pack size |
| 4 | `serializers.ItemSupplierSerializer` via `ItemSupplierViewSet` | `POST`/`PUT`/`PATCH /api/inventory/item-suppliers/` — **the live edit path**, used by the web item form and by ScanTTY | fixed at the root; nothing site-local |
| 5 | `views.ItemSupplierViewSet.mark_discontinued` | flips flags, calls `save()` | fixed at the root — was filing false history |
| 6 | `reorder_queue.services.purchase_orders.void_line_item` | same shape | fixed at the root — same |
| 7 | `admin.ItemSupplierAdmin` / `ItemSupplierInline` | ModelForm save | fixed at the root; already renders `unit_cost` read-only |
| 8 | `services.suppliers.record_price_history` | the audit rows the captain reads | shares one stored-row read with the derivation |

**Deliberate exclusions, with reasons.**

- `PurchaseOrderItem.unit_cost_ordered` / `unit_cost_actual` — a price on an
  ORDER LINE, not a supplier's catalogue price. Different columns, four decimal
  places, its own owner in `reorder_queue`, and its own rules already recorded in
  `AGENTS.md`. Writing one never writes an `ItemSupplier` cost.
- `MaterialUsage.unit_cost` (`views.py:5887`,
  `services/work_order_purchase_bridge.py:130`) — what a work order was charged,
  snapshotted at the moment of usage. Not a supplier price.
- `InventoryItem.unit_cost` — a property (`order_unit_price(self).amount`)
  derived from the links by `inventory.services.pricing`. A read.
- `reorder_queue.services.receiving` — reads the link (UPCs, SKU, lead time) and
  writes `LeadTimeLog`; never writes a cost. The receiving work explicitly
  declined a cost snapshot as a schema change, and that stands.
- Migrations and factories — not operator paths.

## Money and history

**Yes, a false history row was written, and by more than the five symptoms.**
`pricing_changed()` compared the STORED `3.33` against a freshly re-derived,
unrounded `3.3333…`, so **every** save of a link whose case price is not evenly
divisible by its pack size filed a `PriceHistory` row of type `updated` — on a
save that moved no price at all. `mark_discontinued` and `void_line_item` did it
by flipping a boolean. Each of the five symptoms also filed a row recording the
corrupted figure as a real price change: symptom 5 filed
`(updated, 3.33, 9.99, 3)` and symptom 2 filed `(updated, 10.00, 10.00, 1)`.

Both halves are pinned: `test_flag_only_save_writes_no_price_history_row` and
`test_marking_a_supplier_discontinued_files_no_price_change`.

**Existing corrupted rows are NOT rewritten.** A data migration is not this
branch's to authorise.

**CORRECTION, and it matters: an earlier revision of this section introduced the
query below as "the query that finds affected links". That framing was WRONG,**
and a captain acting on it would have worked from close to the opposite of the
remediation set. The predicate finds links whose pair cannot round-trip — links
that were AT RISK — not links that were damaged. It MISSES every link the two
corrupting symptoms actually damaged, and it LISTS healthy ones. What replaced it
is below: the same SQL, relabelled to what it really returns, plus two
history-signature queries and an explicit statement of what none of them prove.

**The corrupted set is NOT recoverable from `inventory_itemsupplier` alone.** A
link corrupted by symptom 5 holds (`package_cost` 9.99, `unit_cost` 3.33, pack
3). That is byte-identical to the row of a supplier who genuinely charges 9.99
for a 3-pack. Current state cannot tell the two apart, because after the drift
the pair is self-consistent — `ROUND(9.99 / 3, 2) * 3 = 9.99`. Only
`PriceHistory` carries the SHAPE of the change, which is why the queries that
follow read history rather than the link table.

Links whose pair cannot round-trip — **AT RISK** from these defects, **NOT known
to be corrupted**:

```sql
SELECT id, item_id, supplier_id, unit_cost, package_cost, quantity_per_package
FROM   inventory_itemsupplier
WHERE  package_cost IS NOT NULL
  AND  quantity_per_package > 1
  AND  ROUND(package_cost / quantity_per_package, 2) * quantity_per_package
       <> package_cost;
```

A link that was ALREADY corrupted does not appear here. After the drift its pair
is self-consistent, so it round-trips cleanly and the predicate passes over it —
(9.99, 3.33, 3) is invisible to this query, and symptom 2's (10.00, 10.00, 1) is
excluded outright by `quantity_per_package > 1`. What this returns is (10.00,
3.33, 3) and its kin: healthy links, where 10.00 is exactly what was paid and
3.33 is the correct rounded derivation, which were merely exposed to the defect.

**A REVIEW LIST of links that may have been corrupted** has to come from
`PriceHistory`. The symptom-5 shape is a case price that moved while the rounded
unit price did not:

```sql
SELECT ph.item_supplier_id, ph.id, ph.recorded_at,
       prev.package_cost AS package_before, ph.package_cost AS package_after,
       ph.unit_cost, ph.quantity_per_package
FROM   inventory_pricehistory ph
JOIN   LATERAL (
         SELECT unit_cost, package_cost, quantity_per_package
         FROM   inventory_pricehistory prev
         WHERE  prev.item_supplier_id = ph.item_supplier_id
           AND  prev.recorded_at < ph.recorded_at
         ORDER  BY prev.recorded_at DESC
         LIMIT  1
       ) prev ON TRUE
WHERE  ph.change_type = 'updated'
  AND  ph.package_cost IS DISTINCT FROM prev.package_cost
  AND  ph.unit_cost IS NOT DISTINCT FROM prev.unit_cost
  AND  ph.quantity_per_package = prev.quantity_per_package;
```

and the symptom-2 shape is a pack size dropping to 1 while the case price held —
same `LATERAL` join, with:

```sql
WHERE  ph.change_type = 'updated'
  AND  ph.quantity_per_package = 1
  AND  prev.quantity_per_package > 1
  AND  ph.package_cost IS NOT DISTINCT FROM prev.package_cost;
```

**NEITHER QUERY IS PROOF.** Their output is a REVIEW LIST, not a set that is safe
to bulk-correct. A supplier can legitimately re-quote a case price without the
rounded unit price moving, and that legitimate change matches the symptom-5
signature EXACTLY. Measured: at `quantity_per_package` 100, a case price moving
12.99 -> 13.00 leaves `unit_cost` at 0.13 in both rows, so a genuine re-quote is
indistinguishable from the defect by this signature. Every row has to be read
against what that supplier actually charged. Anyone bulk-correcting from this
output would be doing the same thing this branch exists to stop: moving stored
money on a rule that cannot tell two cases apart.

Separately, the history rows that may be false — an `updated` row whose figures
match the row before it:

```sql
SELECT ph.id, ph.item_supplier_id, ph.recorded_at, ph.unit_cost, ph.package_cost
FROM   inventory_pricehistory ph
JOIN   LATERAL (
         SELECT unit_cost, package_cost, quantity_per_package
         FROM   inventory_pricehistory prev
         WHERE  prev.item_supplier_id = ph.item_supplier_id
           AND  prev.recorded_at < ph.recorded_at
         ORDER  BY prev.recorded_at DESC
         LIMIT  1
       ) prev ON TRUE
WHERE  ph.change_type = 'updated'
  AND  ph.unit_cost IS NOT DISTINCT FROM prev.unit_cost
  AND  ph.package_cost IS NOT DISTINCT FROM prev.package_cost
  AND  ph.quantity_per_package = prev.quantity_per_package;
```

That last one covers a DIFFERENT shape from the two above — the flag-only save,
where nothing moved at all and a row was filed anyway. It does NOT catch symptom
5, whose false row differs from its predecessor precisely because the case price
drifted.

None of these has been run against production — there is no production data on
this machine (`makerspace_inventory` is empty locally), so the counts are the
captain's to obtain, and so is any decision to act on them. Nothing here
authorises a data migration.

## Cross-project: ScanTTY

Checked against the **real remote default branch** — `uid0/scantty`, `main`,
cloned fresh at `de380e1` (2026-09-01), not a local checkout.

**NO WIRE-CONTRACT CHANGE IS REQUIRED.** `ItemSupplierWrite`
(`internal/omsapi/inventory.go`) already sends `unit_cost` and `package_cost` on
every create and edit with no `omitempty`, so a cleared box is an explicit
`null`. That is exactly the shape the delta rule reads correctly: the form seeds
both boxes from the stored row, so "the operator edited the unit box" arrives as
unit-moved / package-unchanged and is honoured.

**Two behaviour changes its operators will see, both fixes.**

- Editing ONLY the unit-cost box now takes effect. Base re-derived `unit_cost`
  from the stored `package_cost` and silently discarded the edit.
- Clearing the package-cost box now clears the price. Base re-derived it from the
  echoed unit cost and stored `9.99` where `10.00` had been.

**Two things to fix in that repo, neither blocking.**

- `internal/omsapi/inventory.go` lines 1574-1578 document "if package_cost is set
  it wins (unit_cost = package_cost / qty); else if only unit_cost is set,
  package_cost = unit_cost * qty". That is now true on a CREATE, and on an update
  where `package_cost` actually moved — not unconditionally. Stale comment.
- `ItemSupplierFormScreen`'s unit-cost box is editable and not labelled as
  derived. The operator's condition on ruling (C) asks for that wherever the
  figure is editable; the OMS surfaces now carry it, ScanTTY's does not.
  Mitigating: after a save ScanTTY switches to `ItemSuppliersScreen`, which
  re-fetches, so the stored figures are visible immediately.

## How five symptoms passed a green suite

**Every pre-existing test of this behaviour used `quantity_per_package=1`,** the
one pack size at which the derivation is exact and no defect on this path is
reachable. `test_services_suppliers.py`'s whole `TestPriceHistoryViaSave` class —
including `test_non_pricing_save_writes_no_new_history`, which asserts exactly the
guarantee that was broken — is written at pack 1. The fixtures here deliberately
use a pack size that does not divide the case price evenly.

**And four tests in `test_costing.py` asserted a value the database never held.**
`test_trash_bag_example` asserted `unit_cost == Decimal("0.8098")`,
`test_cost_calculation_precision` `0.1299`, `test_updating_package_cost_recalculates_unit_cost`
`2.999` and then `2.499`, `test_edge_case_quantity_per_package_change` `0.075`.
The column is `DecimalField(decimal_places=2)`. Measured on the pre-fix code, with
a `refresh_from_db()` added:

| fixture | test asserted | in memory | on disk |
|---|---|---|---|
| `40.49 / 50` | `0.8098` | `0.8098` | **`0.81`** |
| `12.99 / 100` | `0.1299` | `0.1299` | **`0.13`** |
| `29.99 / 10` | `2.999` | `2.999` | **`3.00`** |
| `15.00 / 200` | `0.075` | `0.075` | **`0.08`** |

Those tests passed only because they read the in-memory object without
refreshing. That divergence is not cosmetic — it is the root of the false
price-history rows, because `pricing_changed()` compared the stored `3.33` with a
re-derived `3.3333…` and concluded the price had moved. `save()` now rounds at the
point of derivation, so the object agrees with the row, and the four tests assert
the stored figure with a `refresh_from_db()` beside it.

These were not a deliberate money fix being undone: they date from
`Test(CI Cleanup.)` / `Fix(CI): More CI Fixes`, and the schema has always
contradicted them.

## The two AST gates

`test_price_single_owner.py` and `test_pack_size_single_owner.py` count direct
column reads per file against an allowlist. Moving the arithmetic out of
`save()` moved reads, so both allowlists were updated — **downwards**:

| gate | file | before | after |
|---|---|---|---|
| price | `inventory/models/core.py` | 7 | **6** |
| pack size | `inventory/models/core.py` | 6 | **3** |

Neither gate was weakened; both still fail on any unlisted reader, and the counts
they permit went down rather than up. `derive_costs` contributes **zero** counted
reads in either gate — it takes the pair and the pack size as parameters and
never touches a model attribute, which is what makes it a definition of the
columns rather than a reading of them. Every reason string was rewritten against
the actual node list, not from memory.

## Findings ledger

| round | ORIGINAL | ARTEFACT | note |
|---|---|---|---|
| 1 — investigation, no code changed | **11** | 0 | the 5 named symptoms, all reproduced on base first, plus 6 nobody had named |
| 2 — implement and verify | **1** | 9 | trend: originals collapsed, artefacts are of round 2's own changes |

**Round 1, the six that were not in the brief's five:**

1. Every save of a link whose case price is not evenly divisible filed a false
   `PriceHistory` `updated` row — including `mark_discontinued` and
   `void_line_item` flipping a boolean.
2. `save()` left an unquantized cost in memory, diverging from the stored row.
   The cause of (1).
3. `_create_supplier_relationship` sent `None` for an ABSENT cost, conflating
   "absent" with "cleared".
4. Symptom 1 (`'0'` for a blank kit cost box) is **live on main**. The earlier
   postmortem states "that fix stays"; it does not — `6c2adc10` put `'0'` back.
5. The `views.py` site named in the brief is CREATE-ONLY; its update branch is
   unreachable. The live edit path is `ItemSupplierViewSet`.
6. The whole pre-existing test set for this behaviour sits at pack size 1.

**Round 2's one ORIGINAL:** the four `test_costing.py` assertions above.

**One ARTEFACT worth naming, because it was self-inflicted and nearly shipped.**
The first cut of `quantize_cost` rounded `ROUND_HALF_UP`. The COLUMN does not:
`DecimalField.get_db_prep_save` quantizes through
`django.db.backends.utils.format_number`, which uses the decimal context default
of `ROUND_HALF_EVEN`. Measured against Django's own helper, the two disagree on
every exact-half case — `package_cost 0.25` at pack 2 would have begun storing
`0.13` where it has always stored `0.12`, and `0.05` at pack 2 `0.03` where it
stores `0.02`. That is a silent change to stored money, which is the exact defect
class this branch exists to end, arriving through the fix for it. Caught before
commit by checking the rounding against `format_number` rather than assuming, and
pinned by `test_a_derived_unit_cost_equals_what_the_column_stores`, which
parametrises both rules' disagreement cases and asserts memory and disk agree.
**Round 2's artefacts, all mine:** seven test labels claiming BEFORE/AFTER for
behaviour that passes on base (corrected to CONTROL); a kit test driving a
hand-rolled copy of the write site instead of the endpoint; a stale kit payload;
a test-database collision from running two pytest sessions at once; two gate
counts; formatting.

**A second self-inflicted one, also caught before it shipped.** The first cut of
the delta rule tested the two "cleared" cases before the two "moved to a value"
cases. That ordering means an operator who empties the case-price box AND types a
unit price gets `NULL`/`NULL` — the figure they just typed discarded without a
word. Every surface that edits these puts both boxes on screen together, so doing
that is ordinary. Base handled it correctly, so the guard ships as a CONTROL:
`test_emptying_the_case_price_while_typing_a_unit_price_keeps_what_was_typed`
passes on base and on the fix, and fails on the ordering it replaces. A value
that MOVED now beats a clear, whichever box it came from — MOVED, not merely
present: the branch is `if unit_moved and unit_cost is not None`, so an ECHOED
unchanged unit cost does not qualify. Stored (`package_cost` 10.00, `unit_cost`
3.33, pack 3), the operator empties only the Package Cost box, and the form sends
every offered field, so the request carries an unchanged `unit_cost` of 3.33 —
nothing moved in that box, and both columns clear.

Round 2's findings are predominantly artefacts of round 2's own changes, which is
the stop condition. Both artefacts that mattered were regressions this branch
would otherwise have shipped, in the same defect class it exists to close — which
is the argument for the invariants being written first and measured against base,
rather than for trusting the fix because it is "at the root".

## The kit `'0'` guard: why it was reverted, and why it comes back

`d07f0528` changed `KitDetailPage.handleSave` from `unitCost === '' ? '0'` to
`? null`. `6c2adc10` put `'0'` back. **It was not reverted because it was wrong.**
The reason is stated in that commit's own `AGENTS.md` edit:

> The kit / supplier-terms **WRITE path has NO entry here, because it moves no
> figure at all.** `KitSerializer._apply_supplier_terms`,
> `KitDetailPage.handleSave`, `ItemSupplierSerializer` / `ItemSupplierViewSet`
> and `inventory/services/suppliers.py` are byte-identical to base `7c078de`.

That branch had withdrawn its whole write-path attempt and needed the path
provably byte-identical to base for its change list to be true. The frontend
guard went with it as collateral, and the `'0'` was re-filed in the same commit as
a BASE DEFECT, filed not fixed — still acknowledged as a defect, just not that
branch's to carry. The reason was branch scope, not correctness.

Restoring it does not reintroduce that reason, because this branch **is** the
write-path branch: "byte-identical to base" is not a claim it makes or wants.
And the one real dependency the guard had is now satisfied. The earlier record
noted the guard made a defect reachable — "Reachable because a blank cost box now
correctly stores NULL" — namely that a costless link later given a unit cost ends
at `unit_cost 5.00 / package_cost NULL`, because `update_or_create` restricts
`update_fields`. That is symptom 4, fixed here and pinned by
`test_a_derived_case_price_is_persisted_on_a_restricted_update`.

## A masking guard removed, and replaced by a fix rather than by nothing

Dropping `_apply_supplier_terms`' `setdefault("quantity_per_package", 1)` is
right — supplying a pack size the operator never gave is the fabrication in
symptom 2 — but that setdefault was **also masking** a defect the earlier branch
measured and then declared dissolved:

> an uncoerced string cost reaches `save()`'s back-fill as STRING REPETITION —
> `"5" * 6 == "555555"`, a valid decimal … That needs a pack size above 1 on a
> costless link, which only became reachable because the partial revert had
> dropped base's forced `setdefault(..., 1)`.

At pack 1 the product is `"5" * 1` and the bug is invisible; that is the only
reason it dissolved. **This branch makes the precondition reachable again**, so
the masking is replaced by an actual fix: `quantize_cost` coerces to `Decimal`
before any arithmetic touches the value. Pinned by
`test_a_string_unit_cost_on_a_case_packed_link_is_not_repeated` on the record's
exact fixture, and mutation-checked — deleting the coercion makes that link store
`Decimal('555555.00')` and the test catch it.

## Still open, filed not fixed

Carried from the earlier record, verified still true, and deliberately NOT taken:

- A non-numeric `supplier` id inside `supplier_terms` reaches the ORM and returns
  500 rather than 400, because that field is a pass-through `DictField`. The same
  holds for a malformed `average_lead_time` and for a cost overflowing the
  column's `max_digits`. Out of scope here: it is an input-validation defect on
  the same endpoint, not a price-derivation one, and this branch neither
  introduces nor worsens it — `quantize_cost` returns an unquantizable value
  untouched so Django's own field validation still raises.
- `KitSerializer._apply_supplier_terms` accepts neither `package_cost` nor
  `quantity_per_package`, so an API caller sending either has it silently
  dropped. Not fixed: the kit form offers no box for either, so there is no
  operator who can supply one, and adding the keys would be scope with no driver.

### The kit form writes one vendor's terms onto another — AMPLIFIED BY THIS BRANCH

Not merely pre-existing: this branch made it move money.

`KitDetailPage` has three ways a figure can reach a supplier link, and only one
of them is now closed. `supplierId` is a free-text box the operator types, and
nothing ties it to what the term boxes hold.

**CLOSED — the cost route through item-level seeding.** `applyKit` no longer
does `setUnitCost(next.unit_cost ?? '')`, so the Unit cost box starts BLANK
rather than pre-filled from `next.unit_cost` — the CHOSEN supplier's figure, via
`order_unit_price(self).amount`. An operator who names a different supplier and
TOUCHES NEITHER BOX now sends `unit_cost: null`; `derive_costs` reaches the
`unit_moved` / `unit_cost is None` clause and re-derives from the named link's
own surviving `package_cost`, so that link stores exactly what it already held
and `pricing_changed` files nothing. Measured on a link for B at (unit 10.00,
package 30.00, pack 3) with the box holding A's 3.33: that save used to store
(3.33, 9.99, 3); with the box blank it stores (10.00, 30.00, 3), untouched.
**The blank box is what closes this route. It is not cosmetic — re-seeding it
reopens the write.**

**STILL OPEN — the cost route through PERSISTENCE.** Nothing ever resets
`unitCost`: not `applyKit`, which runs on every save response, and not the
Supplier box's `onChange`. So a figure typed for one supplier survives into a
save naming another. The trace: the box starts blank; the operator types
supplier A's id and a cost of 5.00 and saves; `applyKit(res.data)` folds the
response in but leaves the box holding 5.00; the operator then names supplier B
and saves again; the payload carries `unit_cost: "5.00"` to B. If B has an
existing link with a case price, `derive_costs` sees `unit_moved` True and
`package_moved` False, so A's figure GOVERNS and B's case price is rewritten
from it, with a `PriceHistory` row asserting B re-quoted. The advisory on that
card fires in exactly this situation — the Supplier box names someone other than
the link the terms on screen came from — but it only says so; it does not
prevent the save.

**STILL OPEN AND UNCHANGED — the SKU route.** `applyKit` still does
`setSupplierSku(next.supplier_sku ?? '')` from the ITEM-LEVEL payload, which is
the chosen supplier's part number, and nothing re-seeds it when the Supplier box
changes. So supplier A's SKU is still written onto whichever supplier is named.
The blanking did not touch this half, and per op-3xsp it is the WORSE half: a
part number gets pasted into an order form, so a misattributed one is
actionable-wrong in a way a price is not.

**On base the cost half was harmless.** An echoed `unit_cost` was discarded
whenever the link had a `package_cost` — symptom 3. Fixing symptom 3 is what
gives a stale figure authority, so this branch converted a display defect into a
money-moving one wherever the named link ALREADY EXISTS and carries a case
price. The new-link case is identical to base: with no stored row there is
nothing to move.

**A fix was attempted on this branch and WITHDRAWN, because it did not
converge.** Three successive rounds each generated the next round's findings in
the same file. The decisive one: closing the "stale box" case by seeding
`supplierId` from the chosen link on load turned every kit save into a
supplier-primary write. `ItemSupplier.supplier_sku` is a non-blank CharField, so
seeding made both halves of `handleSave`'s `supplierId && supplierSku` gate
truthy for every existing kit, `_apply_supplier_terms` forces
`defaults["is_primary"] = True`, and `enforce_single_primary` then demotes every
sibling — so renaming a kit would pin its supplier selection and unflag another
vendor. That escaped the suite only because the fixture behind
`CONTROL: a save that never touches the purchase terms sends none at all`
carries no `supplier_choice` key, which real payloads always do.

**So the next attempt must not start by seeding the id.** The likelier shapes are
a dirty check on the terms themselves (has the operator touched them since load)
or dropping `is_primary` from `_apply_supplier_terms`' defaults on an update —
and whichever is taken, that CONTROL has to be re-run against a fixture that
carries `supplier_choice`, or it proves nothing.

Until then the standing warning is the comment above the attribution line on
that page: changing Supplier there is not a supported way to retarget the terms.

### Lost-update window on the stored-row read

`stored_pricing` issues a plain `SELECT` with no `select_for_update()`. Being
inside `ItemSupplier.save()`'s `transaction.atomic` block makes the write set
commit or roll back together and gives the derivation and `pricing_changed` a
single consistent read, but under PostgreSQL's default READ COMMITTED isolation
it does NOT stop another transaction committing between that `SELECT` and the
`UPDATE`. The trace:

1. Session A reads the stored row: `package_cost` 10.00, `unit_cost` 3.33.
2. Session B commits a price change: 12.00 / 4.00.
3. Session A — a SKU edit that merely echoes 10.00 / 3.33 — computes "nothing
   moved" against its stale read and writes 10.00 / 3.33 back, silently
   reverting B's price change. `pricing_changed` compares against that same
   stale read, so no `PriceHistory` row records the reversal either.

**PRE-EXISTING, not a regression.** Base's `pricing_changed` read was equally
stale, so base loses the same update; this branch neither introduces nor widens
the window.

**One consequence that IS new: the audit trace narrows in this race.** On base,
`pricing_changed` issued its OWN `SELECT` later in the transaction
(`ItemSupplier.objects.get(pk=...)`), and under READ COMMITTED each statement
takes a fresh snapshot — so with B's 12.00 / 4.00 already committed, base
compared its re-derived figures against B's values, reported a change, and filed
a `PriceHistory` row. That row was an accidental trace of the reversal, not a
designed guarantee: it existed only because base read the row twice and the
second read was stale in a different way. This branch reuses the single earlier
read, which the derivation requires, so in that same race `pricing_changed`
compares 10.00 / 3.33 against 10.00 / 3.33 and files nothing — the reversal is
silent where base sometimes left a row. Sharing the read is not the thing to
undo; whoever takes the row-locking branch should know the trace disappears with
it, because locking the read closes both halves at once.

The fix is `select_for_update()` on the read in `stored_pricing`. The operator
deferred it to its own branch and its own review rather than taking it here: row
locking on every `ItemSupplier.save()` is a blast radius of its own, and an
adjacent pre-existing improvement of exactly this shape, authorised alongside a
different change, produced a regression that nearly shipped.
