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
branch's to authorise. The query that finds affected links:

```sql
SELECT id, item_id, supplier_id, unit_cost, package_cost, quantity_per_package
FROM   inventory_itemsupplier
WHERE  package_cost IS NOT NULL
  AND  quantity_per_package > 1
  AND  ROUND(package_cost / quantity_per_package, 2) * quantity_per_package
       <> package_cost;
```

and the history rows that may be false — an `updated` row whose figures match the
row before it:

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

Neither has been run against production — there is no production data on this
machine (`makerspace_inventory` is empty locally), so the counts are the
captain's to obtain.

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
