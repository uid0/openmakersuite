# Third-Party Paper Work Order Form

This document defines the standard PDF template Logistics prints and gives to
third-party vendors. The vendor fills it in (digitally in any PDF reader, or
on paper + scan) and emails it back; the Postmark inbound webhook picks up
the attachment and the `inventory.services.work_order_ingest` pipeline pulls
the structured data back into the `ThirdPartyWorkOrder` row.

## Routing

Inbound submissions are classified as either PM completion (the existing
`WorkOrder` flow) or third-party WO (this flow) by
`detect_submission_kind(pdf_bytes, subject)` in this order:

1. **Email subject prefix `[3PWO]`** — strongest signal; vendors are asked
   to keep the subject line provided on the form.
2. **AcroForm field `third_party_work_order_id` present** — survives a
   PDF round-trip through any reader and is the primary machine signal.
3. **Header text matches `Third-Party Work Order`** — fallback for
   image-only PDFs (paper that was scanned back without OCR).

## Template fields

The form is a single-page (US Letter) PDF. The header line on page 1 must
contain the literal string `Third-Party Work Order` so the text-fallback
detector triggers on scanned paper copies.

### Hidden fields (pre-populated by the generator)

| AcroForm field name           | Purpose                                                                 |
| ----------------------------- | ----------------------------------------------------------------------- |
| `third_party_work_order_id`   | UUID of the `ThirdPartyWorkOrder` this form belongs to. Off-page.       |

A QR code with a `tpwo:<uuid>` payload is also placed on the page as a
secondary recovery path (used when the AcroForm field is stripped by an
aggressive PDF re-encoder).

### Vendor-fillable text fields

| AcroForm field name | Expected content                                | Maps to                                  |
| ------------------- | ----------------------------------------------- | ---------------------------------------- |
| `vendor_name`       | Free text — the legal entity that did the work  | (recorded in `parsed_fields`; advisory)  |
| `invoice_total`     | Numeric, with optional `$`/commas — final total | `actual_invoice_total`                   |
| `downtime_start`    | Datetime — when the asset went out of service   | `downtime_start`                         |
| `downtime_end`      | Datetime — when service was restored            | `downtime_end`                           |
| `keyfob_id`         | Free text — keyfob checked out at site arrival  | `keyfob_id`                              |
| `asset_id_<n>`      | UUID of an additional asset touched by the work | row in `ThirdPartyWorkOrderAsset`        |

Datetime values may use any of these formats; the parser tries them in order:

```
YYYY-MM-DDTHH:MM[:SS]   (ISO-like, no offset)
YYYY-MM-DD HH:MM[:SS]   (ISO-like with space)
YYYY-MM-DD              (date only — interpreted as midnight local)
MM/DD/YYYY HH:MM        (US short)
MM/DD/YYYY h:MM AM/PM   (US 12h)
MM/DD/YYYY              (US date only)
```

Naive datetimes are interpreted in the project's current timezone.

`invoice_total` is parsed by stripping every character that isn't a digit,
decimal point, or sign — so `$1,234.56` and `1234.56 USD` both become
`Decimal("1234.56")`.

Each `asset_id_<n>` field is scanned for the first UUID it contains;
non-UUID values are silently dropped. UUIDs that don't resolve to an
existing `Asset` row are also dropped (no link is created).

### Photo evidence

Any embedded raster image on the page that isn't a QR code is captured as
a `ThirdPartyWorkOrderAttachment` with `kind=photo`. This is a best-effort
heuristic — if a vendor stamps a photo of completed work directly into the
PDF, it ends up linked to the WO automatically; if they email a separate
`.jpg` attachment, the existing attachment-upload endpoint handles it.

## State transition

A successfully ingested 3PWO submission advances the `ThirdPartyWorkOrder`
to `validated` (skipping `in_progress`), since the paper form represents
post-work submission of vendor-reported data. WOs already in
`financial_review`, `closed`, or `cancelled` keep their state but still
absorb the parsed fields (e.g. an updated keyfob id from a corrected
re-submission).

The original PDF is attached as `kind=paper_form` so the maintenance history
shows exactly what the vendor submitted.

## Idempotency

`apply_submission` is safe to call repeatedly. Subsequent applies for the
same submission row update mutated fields but don't add duplicate
`paper_form` or photo attachments — they're keyed on submission id.

For the email path, the Postmark `MessageID` header dedupes at the webhook
boundary: a duplicate delivery returns the original submission's id with
`duplicate: true` and never re-runs the pipeline.
