# `oms-scan-autosubmit-units-and-retry` — the branch record

The derived set, the deliberate exclusions and the evidence for the branch that
made the anonymous QR-scan page file the quantity it shows, and bounded its
retries (`fm/oms-scan-autosubmit-units-and-retry`, base `35d71af`).

**This is the PR body's record, parked here because it is a changelog of one
branch, not standing project knowledge.** `AGENTS.md` keeps only what a future
session needs — the base-unit invariant, the one derivation a filing surface
reads, and the still-open captain decision — under "Three more, found by this
branch's sweeps". Read that first; this file is the evidence behind it.

## Which number the purchasing side receives, and how that was established

`ReorderRequest.quantity` is in BASE units. Four independent lines, checked
against real items rather than taken from the report that opened the task:

1. `packaging.base_reorder_quantity`'s docstring — "The stored
   `ReorderRequest`/`PurchaseOrderItem` quantity stays in base units".
2. `reorder_queue/views.py` `mark-received` does
   `item.current_stock += reorder.quantity`, and `current_stock` is documented on
   `InventoryItem` as always base units. Pinned by
   `test_a_scan_filing_order_quantity_receives_exactly_that_many_base_units`,
   which files 36 anonymously and then POSTs the real
   `reorderrequest-mark-received` action: stock 35 → 71. That test used to
   hand-simulate the receipt (`item.current_stock += reorder.quantity`, then
   assert the difference), which is arithmetically true for every quantity and
   could not fail — it asserted nothing about `mark-received` and is not what
   established this line. It exercises the action now.
3. `ReorderRequest.estimated_cost` multiplies the quantity by a per-BASE-unit
   price (`inventory.services.pricing.order_unit_price`).
4. `inventory/views.py`'s reconciliation auto-reorder already multiplies by
   `count_level.base_units` before storing, its comment naming "storing a pack
   count as if it were base units" as wrong in both directions.

**What was actually filed before the fix**, measured:

| item shape | screen said | POSTed | every other filing path derives |
| --- | --- | --- | --- |
| pack-counting (case of 12) | `3 cases` | `3` | `36` |
| legacy case-based (case of 10) | `4 cases` | `25` | `25` |

The pack-counting row is the worse one and is the one the opening report did not
name: the numeral on screen matches the numeral filed, so it never looked wrong,
and the page ordered a twelfth of the intended amount.

## The derived set

Derived from the question *where does this system show a person a reorder
quantity, or file one on their behalf?* — not from the sites the report named.

### Files a reorder quantity on a person's behalf

| site | derivation | outcome |
| --- | --- | --- |
| `ScanPage` anonymous QR auto-submit | raw `item.reorder_quantity` | **FIXED** → `reorder_display.order_quantity` |
| `ScanPage` signed-in form | `totalUnits` = packages × pack size | already base units; untouched |
| `MaintenanceDashboard` "Create reorder requests & continue" | `alert.reorder_qty` ← raw column | **FIXED** at source (`check_material_stock`) |
| `UniversalScannerPage` scan-to-reorder | literal `1` | excluded — deliberate token (see below) |
| reconciliation auto-reorder | `reorder_quantity × count_level.base_units` | already correct |
| PO pad, optimal quantity, `line_entry.default_quantity` | `base_reorder_quantity` | the root; already correct |
| ScanTTY reorder form | operator types it, prefilled from the supplier pack size | shows what it files |

### Shows a person a reorder quantity

| site | shows | outcome |
| --- | --- | --- |
| `ScanPage` reorder-quantity row (anonymous) | `reorderQuantityLabel` | **FIXED** → the filed quantity |
| `ScanPage` reorder-quantity row (signed-in) | — | **FIXED** → `reorderQuantityLabel`, since the form beside it owns and states the filed number |
| `ScanPage` auto-submit message | `reorderQuantityLabel` | **FIXED** — block replaced by a terminal outcome notice |
| `ScanPage` submitting screen | named nothing | **FIXED** → names the quantity in flight |
| `InventoryList`, `InventoryItemDetailPage` | `reorderQuantityLabel` | excluded — file nothing |
| `TVDashboard` | `request.quantity \|\| item.reorder_quantity` | excluded — fallback unreachable |
| `csvExport` "Reorder Quantity" | raw column | excluded — wants a root fix |
| ScanTTY inventory detail "Reorder qty" | `stockQtyLabel` | excluded — describes, files nothing |

### Deliberate exclusions, with reasons

- **`InventoryList` / `InventoryItemDetailPage`** — the report asked whether the
  same mismatch exists here. It does not: they describe an item and file
  nothing, so `reorderQuantityLabel` is the right answer and there is no
  shown-versus-filed divergence to close.
- **`TVDashboard`** — `/inventory/items/reordered/` returns only items where
  `has_pending_reorder()`, the same predicate that makes `active_reorder_request`
  non-null, so the raw-column fallback beside it is not reachable and shows
  nobody a wrong number.
- **`csvExport`** — the export names `Current Stock`, `Minimum Stock` and
  `Reorder Quantity` with no unit, and for a pack-counting item all three are
  mixed the same way. Correcting one of three identical sites is the narrowing
  fix the branch rules forbid; the column set wants a unit-aware export of its
  own.
- **Index cards** — the printed card names a reorder *threshold*, not a
  quantity. A different value.
- **`UniversalScannerPage`** — files a fixed quantity of 1 by design, labelled
  "Mark for reorder", and shows no quantity at all, so there is nothing to
  diverge. Only its header's false claim was corrected: it marked one side
  effect "member confirmed" and two "auto" when all three fire identically on
  the scan, with no confirmation step in `runSideEffect`.
- **The reconciliation auto-reorder's inline conversion** — it re-derives
  `reorder_quantity × count_level.base_units` rather than calling
  `base_reorder_quantity`, and so omits the shortage top-up. That is a
  deliberate difference in amount, not a wrong unit, so it is outside the scope
  cap.

## The retry storm, and why the obvious fix was wrong

The `useEffect` cleared `submitting` in its catch while `submitting` was one of
its own dependencies, so a failed submit re-entered the effect for as long as
the page was open — 19 POSTs to the public reorder endpoint in 150 ms against a
rejection delayed 5 ms. Latching it to a single attempt had been tried and
reverted, correctly: an anonymous visitor has no manual submit path
(`handleSubmitReorder` returns early on `!isLoggedIn`, and the form is
`isLoggedIn`-gated), so a bare latch trades the storm for a silently dropped
reorder.

What replaced both: the retries live inside ONE awaited loop rather than in the
dependency array, bounded to three attempts with a spreading backoff. The loop
carries no exit condition of its own — every way out is a `return` that has
already put the page into a state the member can see (redirected, or told) — so
there is no silent fall-through. A member ends with a filed request or a notice
naming the item, saying nothing was ordered, and pointing at an action they
already have.

**No retry control was added.** An anonymous visitor has none today, and adding
one changes what such a visitor can *do* rather than what they are told, which
was reserved to the captain.

### The duplicate window the retry opens, narrowed but NOT closed

A bounded retry on a non-idempotent endpoint can file twice. `POST
/reorders/requests/` has no uniqueness or pending-request check
(`ReorderRequestCreateSerializer`), so a first attempt whose row committed but
whose response was lost — a dropped mobile connection, a proxy answering 502
after the write — looks to the page exactly like a failure, and the retry files
a second pending request for the same item.

What this branch does about it: **a retry re-reads the item first**
(`inventoryAPI.getItem`) and, if `has_pending_reorder` has flipped true, stops
and takes the same terminal state a successful submit takes — the member's scan
did result in a filed request, so they are not shown a failure. The re-read
costs no attempt from the bound, and if the re-read *itself* fails the retry
proceeds anyway: a missed reorder is worse than a possible duplicate, and
no-silent-drop is the criterion this effect exists to satisfy.

The **last** attempt asks the same question, and this is a deliberate change to
what an anonymous visitor SEES. Without it, a third POST that commits with its
response lost shows the member "nothing has been ordered … ask a member of staff
to add it to the reorder queue" while a pending request for that item exists —
a false statement on the one screen this effect exists to make truthful, whose
prescribed remedy is precisely the duplicate filing being narrowed. So
`reorderNowPending()` runs once before the notice is rendered, and a `true`
takes the filed terminal state instead. It changes what such a visitor is TOLD,
not what they can DO: no new control, no new endpoint, no permission change. If
that re-read itself fails the notice is still shown — an unverifiable outcome
must be stated, never swallowed.

**This narrows the window; it does not close it.** A server commit that lands
*after* the re-read and before the retry still files twice. Closing it needs
idempotency at the public create endpoint — an idempotency key, or a
pending-request check in the create path — which changes behaviour for every
caller of that endpoint, ScanTTY included. That is a contract change, is routed
separately, and is deliberately **not** taken in this branch. Three tests pin
what was taken: a retry that finds the reorder pending files exactly one POST
and lands the member in the filed state; a re-read that itself fails still
retries to the same bound of three; and a final attempt whose pre-notice
re-read reports the reorder pending reaches the filed state with no failure
notice rendered.

## Anonymous submission is the primary path, and is not narrowed

Most people who scan a shelf label are not registered members; anonymous
scan-to-reorder is what the printed labels exist for. No permission changed, no
login step, no gate: the happy path is still exactly one POST, and the bounded
retry only replaces attempts that were already failing. Two mutations pin it —
gating the effect behind a login, and dropping the filing quantity from the
anonymous payload (which the page refuses to guess at, so its absence would
switch the feature off) — and both fail the build.

## The maintenance low-stock alert: warnings that were silent now appear

`check_material_stock` decided whether to alert with a raw `inv.current_stock >=
inv.minimum_stock`, which mixes the two units this branch exists to separate:
for a pack-counting item `minimum_stock` is a threshold in `count_level` units
while `current_stock` is base units. A material counted in cases of 12 with
`minimum_stock=10` (cases) and `current_stock=24` (2 cases) is low by
`InventoryItem.needs_reorder` and by the reconciliation path's
`count_at_level(item) <= item.minimum_stock`, but `24 >= 10` dropped it here:
the maintenance dashboard showed **no** warning, generated the work order, and
sent a tech to a shelf holding a sixth of its minimum. The action's own
`reorder_qty` had already been moved onto `base_reorder_quantity` six lines
below, so the predicate above it was the one part of the action still outside
the invariant its docstring asserts.

It now reads `if not inv.needs_reorder: continue` — the mode-aware form, and the
documented central chokepoint for the `is_retired` guard this loop used to
re-implement.

**This is a disclosed behaviour change, not a silent one.** Three effects, all
stated rather than discovered later:

- pack-counted materials below their count-level threshold now raise a warning
  they do not raise today — the point of the fix;
- an `each` material exactly AT its minimum now warns (`needs_reorder` is `<=`,
  the old predicate was effectively `<`);
- a **kit** material no longer warns at all. A kit holds no stock of its own —
  receiving one credits its components — so its stock/minimum pair reads as
  permanently low, which is why `needs_reorder` excludes kits at every other
  low-stock surface. This is the one place the change removes an alert, and it
  removes noise the chokepoint already suppresses everywhere else.

Query cost is kept honest: `count_level` was already `select_related` for
`base_reorder_quantity` and `needs_reorder` reads the same relation, and
`item_suppliers` is now prefetched because a legacy case-based item's
`needs_reorder` asks `current_cases` for its shelf pack size — one query for the
whole set rather than one per material.

## Cross-project

ScanTTY was checked against its REAL remote default branch (`uid0/scantty` main
at `de380e1f`), not a local checkout. **No contract change**: `reorder_display`
gained two keys additively and ScanTTY does not read that block; it decodes
`reorder_qty` from `check_material_stock` into `MaterialStockAlert.ReorderQty`
but never renders or files with it, so correcting that value is safe there. Its
own reorder form has the operator type the quantity, prefilled from the
supplier's pack size, so it already shows what it files.

## Evidence

Every check was watched failing against a mutation that defeats it — 20 guards,
10 backend and 10 frontend, including the two anonymous-narrowing mutations
above. One first reported NOT CAUGHT: "the loop is unbounded again" was inert
because the loop's own exit condition was redundant beside the
`attempt >= AUTO_SUBMIT_ATTEMPTS` return. That is why the bound is now a single
expression with no second way out, and why the replacement mutations
(defeating the bound, and giving the loop a silent second exit) both fail.
