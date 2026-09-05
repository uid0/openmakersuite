# `oms-falsy-zero-money-guards` — the branch record

The complete per-figure change list and the withdrawn-attempt postmortem for the
branch that closed the MONEY half of the falsy-guard class
(`fm/oms-falsy-zero-money-guards`, base `7c078de`).

**This is the PR body's record, parked here because it is a changelog of one
branch, not standing project knowledge.** `AGENTS.md` keeps only what a future
session needs — the `inventory.services.pricing` derivation, the two build gates
and their narrowed claims, the wire-type rule, the deliberate exclusions, the
ScanTTY contract and the durable lesson from the withdrawn write-path attempt —
under "What a price costs, and whether we know". Read that first; this file is
the evidence behind it. Cross-references below to "the wire-type table" or "the
exclusions" mean that AGENTS.md section.

## The change list

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
  the "all but one site" shape AGENTS.md records.
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
  `items_without_price` beside it. SUPERSEDED for an anonymous caller:
  `fm/oms-public-inventory-valuation` withheld both keys from a caller with no
  session, so this bullet describes the authenticated payload only. The current
  contract is the `dashboard/inventory-summary/` row of
  [`API_PERMISSION_MATRIX.md`](API_PERMISSION_MATRIX.md).
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
  column is a ratified exclusion (AGENTS.md), and a variance published beside an
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
  branch's own defect — not `ReorderRequest.actual_cost`, which is excluded
  (AGENTS.md).
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
- The kit / supplier-terms **WRITE path has NO entry here, because it moves no
  figure at all.** `KitSerializer._apply_supplier_terms`,
  `KitDetailPage.handleSave`, `ItemSupplierSerializer` / `ItemSupplierViewSet`
  and `inventory/services/suppliers.py` are byte-identical to base `7c078de`.
  Three earlier rounds did change them and their entries stood here; all of it
  was withdrawn. See the withdrawn-attempt record below.
- Supplier detail price-trend **records** (`GET /api/inventory/suppliers/<id>/`,
  `trends[].price_history[].unit_cost`) — `null` -> `0.0` for a recorded zero,
  which is what the supplier-detail chart plots.

## oms-supplier-terms-write-path — an attempt that was made, and withdrawn

**A single owner for the supplier-terms WRITE path was built, gated, and then
reverted in full.** It is recorded here rather than quietly dropped, so the next
session does not retry it one narrow rule at a time.

**ROOT CAUSE, one sentence.** `ItemSupplier.save()` derives `unit_cost` and
`package_cost` from EACH OTHER, so any partial write fights that derivation, and
which cost the operator meant cannot be recovered from the submitted values
alone.

**Three rules were tried at the caller, and each fixed one case by reopening
another:**
1. **Value equality** — a cost clears its twin only when it differs from what is
   stored. Made behaviour turn on a single cent: restating `unit_cost` as
   `5.00` beside a new pack size left the stale package price to overwrite it,
   while `5.01` in the same request was honoured.
2. **Key presence, with a value check** — a cost clears its twin when supplied
   AND different. Discarded a cost the caller had explicitly submitted
   unchanged: the item form sends both boxes, so editing one silently
   re-derived the other.
3. **Key presence alone** — supplying exactly one cost always clears its twin.
   Reopened rule 1's defect from the other side: a link at
   `unit_cost 3.33 / package_cost 10.00 / pack 3` whose SKU was edited had its
   package price re-derived to `9.99` from the rounded unit price, on a save
   that touched no price at all.

Rule 3 removed the guard rule 1 had added, and the next review reported rule 1's
exact defect back. **The owner was not the problem, and neither was any single
rule.** It was built as one named module, routed from every writer the
derivation reached, and gated with an AST build-gate in the manner of the
pack-size and price gates — and it still produced 17 review findings across 6
rounds, 15 of them artefacts of its own changes rather than of the original
defect. **So the next attempt should not open by writing a fourth rule at the
callers.** The place to look is the mutual derivation in `ItemSupplier.save()`
itself: while one column is computed from the other on every save, callers
cannot express "this is the price now" without also implying something about
its twin.

**WHAT WAS KEPT FROM THE OWNER ROUNDS: NOTHING.** That is worth stating with its
reasoning rather than as a bare claim, because two of those fixes looked worth
rescuing and neither was:
- The **item-suppliers identity fix** (a PATCH that changes `supplier` must move
  the addressed row rather than create a second one) repaired a bug that ONLY
  EXISTED because `ItemSupplierSerializer.update` had been routed through the
  owner. Base's plain `ModelSerializer.update` does `setattr` + `save()` on the
  instance the URL names and never resolves a pair, so removing the owner
  returns that path to base, where the duplicate-row bug does not exist. The fix
  had nothing left to fix.
- The **non-numeric supplier-id guard** (a clean 400 instead of a 500) was added
  to close a failure the owner's own create-retry had turned into a misleading
  `DoesNotExist`. Base has the malformed-id 500 too — it is listed below as a
  base defect — but it is base's, not this branch's, and repairing it is part of
  the write path this branch is no longer touching.
Everything else in those rounds lived inside `write_supplier_terms` /
`update_supplier_terms`, its coercers, or its AST gate, all of which are gone.

**BASE DEFECTS ON THIS PATH — SINCE PICKED UP, and no longer listed here.** Six
were filed from this branch, each verified against base `7c078de`. They were
taken whole by `oms-supplier-cost-write-path`, which fixed the derivation itself
rather than the callers: the fabricated pack size, the overwritten unit cost, the
`package_cost` dropped from `update_fields`, the kit form's fabricated `'0'` and
the partial-write echo are all closed and pinned there. Of the six, only the
unvalidated `DictField` (a non-numeric `supplier` id, a malformed
`average_lead_time` or a cost overflowing `max_digits` returning 500 rather than
400) is still open. What remains open on this path is now owned by
[`oms-supplier-cost-write-path-record.md`](oms-supplier-cost-write-path-record.md)
under "Still open, filed not fixed". **Do not work the list from this section** —
it described what was true at the time of the withdrawal.

**One reported defect DISSOLVED with the completed revert, and that was measured
rather than assumed.** Review reported that an uncoerced string cost reaches
`save()`'s back-fill as STRING REPETITION — `"5" * 6 == "555555"`, a valid
decimal — fabricating a package price into `PriceHistory`. That needs a pack
size above 1 on a costless link, which only became reachable because the partial
revert had dropped base's forced `setdefault(..., 1)` while also dropping the
coercion that had masked it. With the `setdefault` restored the product is
always `str * 1`, which is the same string Django coerces to a `Decimal` on
save. Probed on the exact fixture: a costless link at pack 6 sent
`unit_cost: "5"` stores `5.00` and records no fabricated package price.
**That reasoning has since expired**: `oms-supplier-cost-write-path` removed the
`setdefault` as a fabrication and closed the repetition at its source instead —
`inventory.services.suppliers.quantize_cost` coerces to `Decimal` before any
arithmetic runs. The masking is gone and so is the thing it masked; see
[`oms-supplier-cost-write-path-record.md`](oms-supplier-cost-write-path-record.md).

**What this branch still delivers, all of it outside the write path:** the
`inventory.services.pricing` owner with its four named states, `PriceRollup`,
`extended` and `explain`; the price and `estimated_cost` AST gates with their
allowlists and narrowed claims; every read-side routing; the payload and report
honesty changes on the order pad, the purchasing price-trends report, the two
stock-value reports, the public transparency feed and the admin columns; both
refusals with their remedy text; the committee-ledger fix in `receiving.py`;
every frontend consumer fix and corrected wire type; and the ScanTTY contract
record. The acceptance criteria this branch was opened for are met by those; the
write path was an escalation that did not pay off.

**A reported figure that does NOT move, measured and rejected — the wire-type
rule cuts BOTH ways.** Review reported that a PO-form freeform line priced at
`0` passes `canSubmit` and is then silently dropped by
`.filter((item) => item.description && item.unit_cost)` and three sibling
readers, calling it operator input silently discarded. It is not, and the
reasoning inverted the very rule AGENTS.md's wire-type table records: `"0"` is
a truthy STRING in JavaScript — `Boolean("0") === true`, and only `""` is
falsy. The form's `unit_cost` is component state assigned straight from
`e.target.value`, so it is always a string; the falsy-at-zero trap needs a
NUMBER, which is why it bites the property-backed `InventoryItem.unit_cost` and
not a text input.
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
