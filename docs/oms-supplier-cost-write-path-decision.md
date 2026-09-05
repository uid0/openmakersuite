# Supplier cost write path — the one product decision

Status: **ANSWERED — all three as recommended**, with one added condition on (C):
the re-derivation must be OBSERVABLE, so `unit_cost` is presented as derived
wherever it is editable and the write response carries the derived value.

This file is the reasoning that was put to the operator. What was then built, the
full write-price set, the history findings and the ScanTTY check are in
[`oms-supplier-cost-write-path-record.md`](oms-supplier-cost-write-path-record.md).

Measured afterwards and worth knowing before re-reading the reasoning below:
rulings (A) and (B) turned out to CONFIRM behaviour the model already had rather
than change it. Nothing asserted either one, which is precisely how a later round
came to undo one of them.

## What the investigation changed about the question

The brief asks which of `unit_cost` / `package_cost` is authoritative "when they
disagree". Measured against base, most disagreements are **not ambiguous**, and
saying so shrinks the decision to two cases.

Both existing write sites hand the model a *partial* picture and the model then
re-derives from whatever survived. But `ItemSupplier.save()` can read the stored
row — `pricing_changed()` already does. So the model can compute a **delta**
against what is stored, and a delta is well defined regardless of whether a form
*omitted* a field or *echoed it unchanged*. That is precisely the distinction the
withdrawn attempt could not make at the callers, and it is why the three caller
rules each reopened the other's defect.

Under a delta rule:

| unit moved | package moved | pack moved | outcome |
|---|---|---|---|
| no  | no  | no  | derive nothing — both byte-identical (Invariant 1) |
| yes | no  | any | **unit governs**, package re-derives — unambiguous |
| no  | yes | any | **package governs**, unit re-derives — unambiguous |
| yes | yes | any | **(A) genuinely ambiguous** |
| no  | no  | yes | **(B) genuinely ambiguous** |

Rows 2 and 3 need no decision, and they are the common case: every form on every
surface seeds both boxes from the stored row and the operator edits one of them.

**Corrected after the fact — rows 2 and 3 each split on VALUE vs CLEAR too.**
"Moved" covers moving to a value and moving to NULL, and the two do not have the
same answer, so neither row is the single unambiguous case it claims to be.

- Row 2 holds where `unit_cost` moved to a VALUE. Where it moved to a CLEAR the
  outcome is the OPPOSITE of "unit governs": stored (unit 3.33, package 10.00,
  pack 3) with `{unit_cost: null, package_cost: "10.00"}` re-derives the unit
  cost from the surviving case price and 3.33 comes back. The derived box cannot
  be cleared on its own. Pinned by
  `test_clearing_only_the_unit_price_re_derives_it_and_says_so_in_the_response`.
- Row 3 holds where `package_cost` moved to a VALUE. Where it moved to a CLEAR
  the unit cost does not re-derive — both columns clear, because the
  authoritative cost is gone and "no price on file" has to stay sayable. Pinned
  by `test_clearing_the_case_price_clears_both`.

**Corrected after the fact — row 4 is not one case but two.** As written it
routes the cleared-package / moved-unit edit to (A), whose answer is "package
governs", which for a CLEARED package means both columns clear. That is the
opposite of what shipped. Row 4 holds only where `package_cost` moved to a
VALUE; where it moved to a CLEAR and `unit_cost` moved to a value, the unit
price governs and the case price re-derives from it. Trace: stored (10.00, 3.33,
pack 3), request `{package_cost: null, unit_cost: "4.00"}` stores (4.00, 12.00),
pinned by
`test_emptying_the_case_price_while_typing_a_unit_price_keeps_what_was_typed`.
See (A) below.

## (A) Both costs moved in one request, and they disagree

**Recommendation: `package_cost` governs; `unit_cost` re-derives from it.**

**Corrected after the fact, against what shipped:** this holds where
`package_cost` moved to a VALUE. It does NOT hold where the operator CLEARED the
case-price box and typed a unit price in the same edit — there the typed
`unit_cost` governs and the case price re-derives from it. Reading (A) as
covering that case too would mean discarding a figure the operator had just
typed, which is the defect class this work exists to close, and the first cut of
the delta rule did exactly that before it was caught. The shipped rule is "a
value that MOVED beats a clear, whichever box it came from"; stored (10.00, 3.33,
pack 3) with `{package_cost: null, unit_cost: "4.00"}` stores (4.00, 12.00),
pinned by
`test_emptying_the_case_price_while_typing_a_unit_price_keeps_what_was_typed`.
An ECHOED unit cost is not a moved one, so clearing the case price on its own
still clears both. The reasoning below stands for the case it actually decides.

Four independent surfaces already assert this, none of them added by this work:

1. `ItemSupplier.unit_cost.help_text` — read "Cost per individual unit from this
   supplier (**auto-calculated from package cost**)" when this was put to the
   operator; it now reads "Cost per individual unit from this supplier.
   **Derived from the package cost**; changing it re-prices the package", which
   says the same thing and adds what a change to the box does.
   `package_cost.help_text` is unchanged — "Total cost for one package from this
   supplier (**what you actually pay**)".
2. `inventory/admin.py` — `ItemSupplierAdmin.readonly_fields` contains
   `unit_cost`; the inline offers `package_cost` for edit and `unit_cost_display`
   read-only. The admin already treats unit as derived.
3. `inventory/views.py::_process_cost_data` — "Prefer package_cost if provided".
4. ScanTTY `internal/omsapi/inventory.go`, remote `main` @ `de380e1` — "if
   package_cost is set it wins (unit_cost = package_cost / qty)".

Choosing `unit_cost` would make three of those four wrong and would require a
contract change in a second repo.

The arithmetic agrees. `package -> unit` is the lossy direction (`10.00 / 3` is
not representable at `decimal_places=2`); `unit -> package` is exact. Letting
unit govern means an invoice-accurate case price gets overwritten by one
reconstructed from a rounded unit price — which is exactly the `10.00 -> 9.99`
defect, symptom 5.

## (B) Only the pack size moved

**Recommendation: hold `package_cost`, re-derive `unit_cost`.**

Correcting "this case actually holds 6, not 3" is a statement about the pack, not
about what the supplier charges for a case. Holding `unit_cost` fixed instead
would silently double the recorded case price on a pack-size correction —
inventing a price the supplier never quoted. This is also the only choice
consistent with (A), which matters because the brief requires that
"authoritative" survive a pack-size change.

## (C) Clearing exactly one of the pair — lower stakes, same decision

ScanTTY sends an explicit `null` to clear a cost box (no `omitempty`, documented).
On base, `null` never cleared: the surviving cost re-derived the cleared one.
(`AGENTS.md` recorded that as known base behaviour; its supplier-cost section now
states the shipped rule instead.)

**Recommendation: clearing the authoritative cost (`package_cost`) clears both;
clearing only the derived cost (`unit_cost`) re-derives it.**

Clearing the case price is the operator saying "I do not know what this costs",
and "no price on file" must be reachable — that is the whole thesis of the
falsy-zero-money-guards work this branch follows. Blanking the derived box alone
is not meaningful, so re-deriving it is right and is also today's behaviour.

Rejected alternative: "clearing either clears both" — an operator blanking only
the unit box would silently destroy the case price they never touched.
