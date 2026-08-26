# Purchase-Order Receiving API

The contract for recording what physically arrived against a purchase order.
Two clients drive it — the OpenMakerSuite web UI and ScanTTY — so this document
is the specification, not a description of what the web screens happen to do.
A client that follows this page needs to read no application code.

Errors follow [`API_ERROR_CONTRACT.md`](API_ERROR_CONTRACT.md). Every endpoint
below requires an authenticated user.

---

## The flow

1. **Pick the order.** `GET /api/reorders/purchase-orders/{id}/receiving/`
2. **Pick or scan the line.** Match a scanned code against the worksheet's
   `lines[].scan_codes`.
3. **Scan the tracking barcode.** Send it as `tracking_number`.
4. **Say how much arrived.** `quantity_received` per line — including more than
   was ordered.
5. **Capture serials**, with optional lot and expiry, on lines that have
   `serial_targets`.
6. **Finish the order off.** It advances to `received` on its own once every
   line is settled; `POST .../mark-received/` closes out whatever is left.

Steps 3–5 are all part of one `POST .../receive/` call.

---

## Principles this API is built on

These are decisions, not implementation details. A client that fights them will
produce a dishonest record.

**A mismatch is recorded, never rounded.** `quantity_received` is stored as
sent. Receiving 12 against an order for 10 is accepted and flagged
`over_received` with `quantity_variance: 2`. The API will never quietly reduce
your figure to the ordered one, and a client must not do so either.

**Short is not the same as outstanding.** Receiving 8 of 10 leaves the line
`partially_received` with 2 still expected — the rest may be on a backorder. A
line becomes `closed_short` only when somebody explicitly says the balance is
not coming. Flagging every partial receipt as short would raise a vendor query
on every backorder.

**Serials belong to the item that goes on the shelf.** On a kit line that is
the kit's *components*, never the kit itself: a kit is bought as one SKU and
stocked as its parts, so its own stock is permanently zero and a serial against
it names a unit that can never be drawn down. Read `serial_targets`; never infer
serializability from the line's `item_details.is_serialized`, which on a kit
line describes the kit.

**Nothing you send is silently dropped.** Over-supplying serials, naming an item
the line does not credit, or repeating a serial are all `400`s with an
explanation — never a truncation. A rejected receipt writes nothing at all: the
whole call is one transaction.

**Transit duration is not computed.** Goods are not always recorded on the day
they arrive, so any figure derived from `delivery_date` would be wrong. What is
stored instead is enough to compute it later once the definition is settled:
the tracking barcode, and an accurate receipt timestamp (see
[Timestamps](#timestamps)).

---

## `GET /api/reorders/purchase-orders/{id}/receiving/`

The receiving worksheet. Read-only, derived from the order on every request —
there is no stored worksheet to invalidate, and a receipt recorded by another
client shows up on the next fetch.

Two kinds of identifier appear below and they are not interchangeable. A
purchase order and a purchase-order line are **integers**; an inventory item is
a **UUID string**. `purchase_order`, `purchase_order_item` and `{id}` in the
URLs are integers; `item` on a line, in `serial_targets` and in `serial_gap` is
a UUID.

```json
{
  "purchase_order": 412,
  "po_number": "PO-2026-0500",
  "supplier": "Grainger",
  "status": "partially_received",
  "status_label": "Partially Received",
  "can_receive": true,
  "unavailable_reason": null,
  "is_settled": false,
  "is_fully_received": false,
  "has_receipt_variance": false,
  "outstanding_line_count": 1,
  "variance_line_count": 0,
  "lines": [
    {
      "purchase_order_item": 301,
      "label": "Stocked Bolt",
      "item": "a71f…",
      "item_type": "inventory_item",
      "quantity_ordered": 10,
      "quantity_received": 3,
      "quantity_pending": 7,
      "quantity_variance": -7,
      "receipt_state": "partially_received",
      "receipt_state_label": "Partially received",
      "is_settled": false,
      "is_voided": false,
      "is_closed_short": false,
      "closed_short_reason": "",
      "is_kit_line": false,
      "scan_codes": [
        { "code": "BOLT-1", "kind": "item_sku" },
        { "code": "0123456789012", "kind": "package_upc" }
      ],
      "serial_targets": [],
      "serials_recorded": 0
    }
  ]
}
```

### `can_receive` and `unavailable_reason` are a pair

They answer different questions and a client must not collapse them:

| Situation | `can_receive` | `unavailable_reason` | `outstanding_line_count` |
| --------- | ------------- | -------------------- | ------------------------ |
| Order is in flight, work to do | `true` | `null` | `> 0` |
| Order is a draft | `false` | "This order is still a draft…" | may be `> 0` |
| Receiving has finished with it | `false` | "Receiving has finished with every line…" | `0` |
| Cancelled / voided | `false` | "This order was cancelled…" | may be `> 0` |

"You may not receive against this, and here is why" is a different fact from
"there is nothing left to receive". An operator standing at the bench with a box
acts differently on each: one means go and send the order, the other means the
box is a surprise. Show the reason; do not just hide the button.

### `scan_codes`

Every identifier a scanner could plausibly read off this line's goods, so a
client can resolve a scan locally without a round trip.

| `kind` | Source |
| ------ | ------ |
| `item_sku` | Our own SKU for the item |
| `package_upc` | Barcode on the outer box |
| `unit_upc` | Barcode on a single unit |
| `supplier_sku` | The vendor's number, as it appears on a vendor-applied label |

Blank identifiers are **omitted**, never emitted as `""`. An empty code in the
list would match a stray empty scan against every unbarcoded line on the order.
An empty `scan_codes` array is a real answer — "this line cannot be scanned to"
— and is not the same as "no match found". Asset and freeform lines always come
back empty.

### `serial_targets`

The inventory identities a receipt on this line may record serials against, and
how many units of each the **full ordered quantity** implies.

```json
"serial_targets": [
  {
    "item": "c9a2…",
    "item_name": "Meter",
    "item_sku": "M-1",
    "serial_tracking_mode": "reusable",
    "quantity": 2
  }
]
```

* Ordinary inventory line → the line's own item, if it is serialized.
* **Kit line → each serialized component**, at `quantity_per_kit × ordered`. The
  kit itself never appears.
* Nothing serialized, or an asset/freeform line → `[]`.

`serial_tracking_mode` reflects the item **as it is today**, not as it was when
the order was placed: the kit snapshot freezes what a receipt *credits* (which
components, how many), but whether the system tracks a component serially is a
live property, so a component that became serialized after the order is still
offered.

> Serialized items were once forbidden as kit components entirely. That rule was
> lifted — receiving can record a serial against the right component, so
> refusing the configuration guarded against nothing. What guards the identity
> rule now is the receipt itself (naming a kit is refused) plus
> [`serials_outstanding`](#serials_outstanding).

For a partial receipt, scale: `round(target.quantity / quantity_ordered ×
quantity_received)`. This is the same function the receipt validates against, so
this endpoint can never offer an identity the receipt would then refuse.

---

## `POST /api/reorders/purchase-orders/{id}/receive/`

Record a receipt. Requires the order to be `sent`, `confirmed`, or
`partially_received` (see `can_receive`); anything else is a `400`.

### Request

```json
{
  "items": [
    {
      "purchase_order_item": 301,
      "quantity_received": 8,
      "at_level": false,
      "serials": [
        { "serial_number": "SN-001", "item": "c9a2…", "lot": "LOT-42",
          "expiration_date": "2027-01-31" },
        { "serial_number": "SN-002" }
      ],
      "close_short": true,
      "close_short_reason": "backorder cancelled"
    }
  ],
  "delivery_date": "2026-08-25",
  "tracking_number": "1Z999AA10123456784",
  "carrier": "UPS",
  "receipt_notes": "Two boxes, one crushed corner"
}
```

| Field | Required | Notes |
| ----- | -------- | ----- |
| `items[].purchase_order_item` | yes | Must belong to this order and not be voided or already closed short. |
| `items[].quantity_received` | yes | ≥ 1. **May exceed the outstanding quantity** — see below. |
| `items[].at_level` | no | `true` means the quantity is a count of whole packs of the item's `count_level` ("three cases came in"), converted to base units before anything else. Invalid on a line whose item is not counted in packs, and on asset/freeform lines. |
| `items[].serials` | no | See [Serials](#serials). |
| `items[].close_short` | no | Write off whatever is still outstanding *after* this receipt. |
| `items[].close_short_reason` | no | Recorded on the line. |
| `delivery_date` | no | The date the **operator states**. Defaults to now. See [Timestamps](#timestamps). |
| `tracking_number` | no | The carrier's tracking barcode, stored verbatim as scanned. Max 100 chars. |
| `carrier` | no | Max 100 chars. |
| `receipt_notes` | no | Free text. |

The same line may appear several times in one request (two boxes of the same
part); the quantities add up.

The response is the full purchase-order object, including the updated
`status`, `has_receipt_variance` and `variance_line_count`.

### Over-receipt

Sending more than is outstanding is **accepted**, not an error:

* `quantity_received` becomes the figure you sent;
* `quantity_variance` goes positive and `receipt_state` becomes `over_received`;
* the stock credited is what actually arrived — for a kit line, the components
  of every kit that turned up, not just the ordered ones;
* the order's `has_receipt_variance` becomes `true` and stays true.

A client should tell the operator before sending (a typo is easier to fix than a
vendor query), but must send the real figure once confirmed.

### Serials

Each entry:

| Field | Required | Notes |
| ----- | -------- | ----- |
| `serial_number` | yes | Trimmed; must not be blank. |
| `item` | when the line credits >1 serialized identity | Which identity the serial belongs to. Optional when there is exactly one. Naming a kit is always refused. |
| `lot` | no | Batch number, recorded verbatim. Does not affect stock. |
| `expiration_date` | no | ISO date. Recorded and displayed only — an expired unit still counts as on-hand and raises no alert. |

Each serial becomes a `SerializedComponent` in `in_stock` status, carrying both
provenance links (the delivery line it came in on and the purchase-order line it
was ordered against), created **inside the receipt's transaction**.

**Fewer serials than units is allowed.** Goods that physically arrived must be
recordable even when not every label has been scanned yet; the gap shows up as
[`serials_outstanding`](#serials_outstanding).

**More serials than units is a `400`**, never a truncation. So is:

| Condition | Message contains |
| --------- | ---------------- |
| `item` names the kit on a kit line | `never itself stocked` |
| `item` omitted on a line crediting several serialized identities | `which one it belongs to` (and names them) |
| `item` names something this line does not credit | `does not credit a serialized unit` |
| More serials than the receipt credits | `only credits N unit(s)` |
| Same serial twice in one request | `appears twice` |
| Serial already on file for that item | `already recorded` |
| Serials on a line with nothing serialized | `nothing on this line is serialized` |

Any of these rolls back the **entire** receipt — no stock is credited, no
delivery is created. Fix and resend.

---

## `POST /api/reorders/purchase-orders/{id}/close-short/`

Write off the outstanding balance on named lines as never arriving. This is how
a short receipt *ends*.

```json
{ "items": [ { "purchase_order_item": 301, "reason": "backorder cancelled" } ] }
```

The line becomes `closed_short` and `is_settled`, while `quantity_received`
stays at what actually arrived and `quantity_variance` stays negative. The
order advances to `received` if this settles the last outstanding line.

Refused with a `400` when the line is already closed short (the first reason and
actor are a record, not a draft), is voided, or has nothing outstanding.

Equivalent to setting `close_short` on the line in a `receive` call; use that
form when the shortfall and the receipt are one operator action.

A line that is closed short is refused by `receive` — the balance has been
written off, so there is nothing to receive against. Correct a close-short made
in error with `reopen-short/` below, then receive.

---

## `POST /api/reorders/purchase-orders/{id}/reopen-short/`

Take back a close-short. This is the correction for one recorded in error, and
the action `receive` points you at when it refuses a closed-short line.

```json
{ "items": [ { "purchase_order_item": 301, "reason": "closed the wrong line" } ] }
```

**A correction, not an undo.** The close-short stays on the line exactly as it
was recorded — `closed_short_at`, `closed_short_by` and `closed_short_reason`
keep their values — and the reopen is stamped beside it to the same standard:
`reopened_at`, `reopened_by` / `reopened_by_username`, `reopened_reason`. A
client reading the line afterwards sees both, and the history reads as a mistake
and its correction rather than as a clean slate. `was_reopened` is `true`.

`is_closed_short` is derived from the two stamps together, so `receipt_state`
goes back to `not_received` or `partially_received` and `is_settled` back to
`false` with no separate flag for a client to reconcile.

After a reopen:

* the line is outstanding again and `receive` accepts it;
* the order's status is re-derived in the same transaction — one that had
  reached `received` drops back to `partially_received`, because it is again
  waiting on something;
* the line may later be closed short again; the newer stamp is the one in force.

Unlike every other receiving write, this is accepted on an order whose status is
already `received` — a line closed short in error is usually noticed *after* the
close settled the order. Allowed statuses are `sent`, `confirmed`,
`partially_received` and `received`; a draft, cancelled or voided order is a
`400`, because there is no receiving to correct.

Refused with a `400` when the line is not currently closed short, rather than
stamping a correction over nothing.

The close-short and the reopen are two separate, separately attributable rows on
the purchase order's audit trail (`po_receive_items` and
`po_line_reopen_short`); the reopen's metadata names the close-short it corrects.

---

## `POST /api/reorders/purchase-orders/{id}/mark-received/`

Finish the order off. Closes **every still-outstanding line** short, recording
the optional `reason` against each, and advances the order to `received`.

```json
{ "reason": "vendor closed the order" }
```

Lines already received in full are untouched — closing an order out never
invents a shortfall on a line that landed.

> **Not the same as `mark-delivered`.** That endpoint asserts the opposite —
> that every outstanding quantity *did* arrive — and receives and stocks it.
> This one stocks nothing and writes the shortfall off. Never substitute one for
> the other; the difference is exactly the difference between an honest record
> and a tidy one.

Refused with a `400` when the order has nothing outstanding, rather than
silently doing nothing.

---

## `serials_outstanding`

Units of a serialized identity that a receipt has already put into stock, for
which no serial number has been recorded.

Present on every purchase-order line, on the order (summed), and on each
worksheet line — where `serial_gap` breaks it down per identity:

```json
"serial_gap": [
  { "item": "c9a2…", "item_name": "Meter", "expected": 3, "recorded": 2, "outstanding": 1 }
],
"serials_outstanding": 1
```

`expected` is derived from the quantity **received**, not ordered, so this is
real outstanding work rather than a restatement of the order. `0` always means
nothing is owed — a line with nothing serialized reports `0` and an empty
`serial_gap`, which is not the same as a line that owes serials nobody looked
for.

This figure exists because **every** receive path can put a serialized unit into
stock without a serial:

| Path | Can leave a gap? |
| ---- | ---------------- |
| `receive` with full serials | no |
| `receive` with partial or no serials | yes — deliberately allowed |
| `mark-delivered` | yes — it captures no serials at all |
| `receipts/scan_barcode/` | yes, on non-kit lines |

Serialized items used to be banned from kits on the grounds that receiving a kit
would "credit stock without recording serial numbers". That was true, but never
unique to kits — `mark-delivered` has always done exactly that to an ordinary
serialized line. Reporting the gap covers every path, does not block a receipt
for goods that physically arrived, and leaves work that can be finished later;
the prohibition covered one path, blocked a legitimate configuration, and pushed
the operator somewhere the system could not see.

A client should surface a non-zero value. It is not an error.

---

## Line receipt states

`receipt_state` is derived from the quantities and the close-short stamp; it is
never stored, so it cannot drift from them.

| State | Meaning | `is_settled` |
| ----- | ------- | ------------ |
| `not_received` | Nothing has arrived yet | no |
| `partially_received` | Some arrived; the rest is still expected | no |
| `received` | The full ordered quantity arrived | yes |
| `over_received` | More arrived than was ordered | yes |
| `closed_short` | Less arrived, and the balance was written off | yes |
| `voided` | The line was struck off the order | yes |

A `closed_short` line that is reopened leaves this table by the way it came in:
it returns to `not_received` or `partially_received` and `is_settled` goes back
to `false`, while `closed_short_at` and the reopen stamped beside it both stay
on the record. See [`reopen-short/`](#post-apireorderspurchase-ordersidreopen-short).

`is_settled` means "receiving is finished with this line" and is what decides
whether it still blocks the order. It is **not** `is_fully_received`: a line
closed two units short is settled and not fully received, and both facts stay on
the record.

At order level:

| Field | Meaning |
| ----- | ------- |
| `is_fully_received` | Every active line got at least its ordered quantity. Stays `false` for ever once a line is closed short — the honest answer to "did everything turn up?" |
| `is_settled` | Receiving is finished with every active line. **This** is what advances the order to `received`. |
| `has_receipt_variance` | Some line arrived short or over. Survives the order closing — that is the point. |
| `outstanding_line_count` | Active lines still being waited on. |
| `variance_line_count` | Settled lines that did not match the order. |

A voided line is settled and is *not* a variance: it was struck off, so nothing
is coming and nothing is owed.

---

## Timestamps

Two different times are recorded, and they answer different questions.

| Field | What it is |
| ----- | ---------- |
| `OrderDelivery.delivery_date` | The date the **operator states** the goods arrived. Accepts a date, stored at midnight. Trust it as a business date, never as a clock. |
| `OrderDelivery.created_at` | When the receipt was actually taken, to the second. Set by the server; not settable by a client. |

Transit duration is deliberately **not computed** anywhere in this API. The
inputs for computing it later are all stored: `tracking_number` (the parcel),
`created_at` (an accurate receipt time), and — on the line —
`actual_shipment_date`, the date the supplier reported the line shipped. There
is no server-side dispatch timestamp beyond that per-line date; a definition of
transit duration will have to choose between it and the tracking carrier's own
data.

---

## Related endpoints

| Endpoint | Use |
| -------- | --- |
| `POST .../mark-delivered/` | Everything outstanding arrived — receive and stock it all in one delivery. |
| `POST /api/reorders/receipts/scan_barcode/` | A separate, older inline receive path keyed on UPC. Refuses kit lines, voided lines and lines closed short, refuses more than the outstanding quantity (unlike `receive`, it does not accept an over-receipt), and writes no audit event. It settles the order through the same derivation as `receive`, so it leaves `status` agreeing with the lines. Prefer `receive` with a `scan_codes` match. |
| `POST /api/inventory/serialized-components/scan_receive/` | Accession a serial with no purchase order behind it. |
| `GET .../item-lookup/?q=` | Resolve an identifier against the *supplier catalogue* when **adding** a line. Not for receiving — use the worksheet's `scan_codes`. |
