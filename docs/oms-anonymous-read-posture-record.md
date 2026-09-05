# `oms-anonymous-read-posture` — the branch record

The evidence behind the captain's decision that vendor identity and vendor
pricing sit behind a login (`fm/oms-anonymous-read-posture`, base `ace8247`).

**This is the PR body's record, parked here because it is a changelog of one
branch, not standing project knowledge.** `AGENTS.md` keeps only what a future
session needs — the `inventory.services.vendor_visibility` owner, the two
shapes, the omitted-not-nulled consequence, the upload-field unit and the
vacuous-check rule — under "Vendor identity and vendor pricing are behind a
login". Read that first; this file is what was measured.

**The current contract is not here either.** Which keys each serializer
withholds, which media prefixes are gated, what stays open and why, and what
the permission matrix now resolves all live in
[`API_PERMISSION_MATRIX.md`](API_PERMISSION_MATRIX.md). This file records what
was true before the change and how it was demonstrated.

## The instrument was committed red, before anything was closed

`config/tests/test_anonymous_vendor_exposure.py` landed first, on `88ef3f1`,
where **20 of its 24 tests failed on 21 distinct paths**. Every closure below is
therefore proven by a check that was watched failing. The two tests that passed
on that commit are the CONTROLS and must keep passing: the crawl detects a
known-public sentinel (so a green run is not an artefact of a broken probe), and
anonymous scan-to-reorder plus issue reporting work end to end.

The probe issues real unauthenticated requests against the live URL conf. It
does not read `permission_classes`, because at that commit the matrix recorded
only DECLARED classes and misreported by construction every viewset overriding
`get_permissions` — and had already been cited to conclude that a screen was
anonymously readable when it was not.

## Before and after, per surface

**Closed outright** — endpoints with no non-vendor half. Every one answered an
anonymous GET with 200 and vendor data at base; every one now answers 401/403,
and an authenticated CONTROL proves the data is gated rather than deleted:

| Path | What an anonymous caller got at base |
| --- | --- |
| `/api/inventory/suppliers/` and `<id>/` | Every vendor's name, contact and terms |
| `/api/inventory/suppliers/<id>/analytics/` | Per-vendor spend and performance |
| `/api/inventory/supplier-agreements/` and `<id>/` | Contracts and standing quotes |
| `/api/inventory/item-suppliers/` and `<id>/` | The link rows: SKU, UPC, both costs, lead time |
| `/api/inventory/item-suppliers/<id>/price_history/` | What we have paid over time |
| `/api/inventory/price-history/` and `<id>/` | The same, unscoped |
| `/api/reorders/purchase-orders/` and `<id>/` | Orders with vendor and money. `PurchaseOrderViewSet.get_permissions` returned `AllowAny` while its declared class said `IsAuthenticatedOrReadOnly` — exactly the shape the matrix could not see, which is why a real request pins it |

**Field-gated** — endpoints the anonymous QR-scan flow or the public
transparency page runs on. Each stays 200 for a caller with no session and
carries no vendor sentinel:

`/api/inventory/items/`, `items/<id>/`, `items/<id>/metrics/`,
`items/<id>/kits/`, `items/?with_metrics=1`, `items/low_stock/`,
`items/reordered/`, `items/<id>/download_card/`,
`fixtures/<id>/download_card/`, `/api/reorders/analytics/transparency/`.

Two of those were found by reading rather than by the crawl:
`FixtureViewSet.download_card` rendered the refill item's card, lead-time lines
and all, to anonymous callers; and the transparency action carried
`authentication_classes=[]`, which made `request.user` `AnonymousUser` for
EVERY caller including a signed-in one, so no gate placed there could have
worked. Removing that line is the load-bearing edit on that endpoint.

The transparency line the captain was shown: anonymous callers keep the
aggregate totals and `po_number`, and lose supplier names and all per-order
money. The three key sets are named constants in `reorder_queue/views.py`
(`ORDER_VENDOR_KEYS`, `LEDGER_VENDOR_KEYS`, `PO_VENDOR_KEYS`), so widening the
page is moving a key out of a tuple. The page's own `transparency_note` and its
footer claim ("ALL financial information is made available") were reworded for
the reader they have rather than left making a claim the payload no longer
honours.

## Media, before and after, through real nginx

Base `nginx/templates/default.conf.template` carried a single
`location /media/ { alias /app/media/; expires 7d; }`. Verified rather than
assumed: nginx rendered from that template, with a real Django process behind it
and a seeded agreement on disk, answered an anonymous GET for
`/media/supplier_agreements/...` with **HTTP 200 and the file's bytes**, cached
publicly for a week.

After, through the same nginx: every gated prefix 403s anonymously and 200s with
a session, `/media/inventory/qrcodes/` stays 200 for everyone, and the operator
receives the real bytes. The first pass gated four prefixes; re-deriving the
question as "where can a vendor document be STORED?" over every `upload_to`
under `backend/` found **five more** — including a callable-valued one a
string-literal sweep cannot see, and two roots fed by the Postmark inbound
webhook whose contents are whatever a vendor emailed in. The nine, and the
open prefixes with their reasons, are in
[`API_PERMISSION_MATRIX.md`](API_PERMISSION_MATRIX.md); the enforced copy is
`config/tests/test_upload_field_classification.py`.

## What the crawl could not see, and what that cost

A green run from an instrument is a claim about what the instrument can reach.
Asking what this one could NOT reach produced two real disclosures and three
repairs to the instrument itself:

* **Writes.** A crawl issues no POSTs, so it proved nothing about the 22 routed
  actions that resolve to `AllowAny` on POST. Two of them were leaking:
  `POST /api/scanner/dispatch/` answered an anonymous UPC scan with
  `supplier_name` and `item_supplier_id` — a UPC is printed on the outside of
  the box, so anyone holding one could turn it into the name of the vendor we
  buy from — and `POST /api/inventory/items/<id>/log_usage/` replied with a
  `UsageLogSerializer` row, which is `fields = "__all__"` and so carried
  `unit_cost` and `total_cost`. The same serializer nests on the item payload as
  `recent_usage`, so the gate went on the serializer. Every anonymous write is
  now exercised by hand from the `anonymous_write_surfaces` fixture.
* **A fixture that made a surface look empty.** A nested serializer over an
  empty relation serialises to `[]` and reads as clean: `recent_usage` tested
  green only because nothing had been consumed. The seed now carries a row for
  every nesting the gate depends on — usage log, fixture, purchase order,
  agreement.
* **A pk that 404s.** A DRF router pk is untyped, so `__uuid__` never applied
  and `/api/inventory/items/<a supplier's id>/` 404'd — the largest vendor
  payload there is was never fetched, silently. The fill is route-aware now.
* **Unfillable format-suffix routes.** `suppliers.json` and its siblings could
  not be built, so every vendor endpoint had a second registered path the crawl
  never touched. Fixed by filling them rather than by arguing they resolve to the
  same view.
* **A sentinel the code could not route.** `scanner.resolvers` treats only a
  pure-digit payload as a UPC, so the ZZQQ-prefixed sentinel never reached the
  UPC path and that surface tested clean while being open. The UPC sentinels are
  real 12-digit barcodes now.

PDFs are decoded before they are searched, because `download_card`'s leak is
invisible to a byte grep, and
`test_anonymous_vendor_exposure_coverage.py::test_the_crawl_could_read_every_pdf_it_was_served`
fails if a PDF the crawl was served could not be read.

Two floors keep a future green run honest:
`test_anonymous_vendor_exposure_coverage.py` asserts at least **400** requests
actually built and at least **50** routes answering **200**, so a change that
quietly makes most routes unfillable fails rather than passes.

**Reported and deliberately NOT fixed here:** an `@action` whose signature omits
`format=None` raises `TypeError` (a 500) on its DRF format-suffix route —
`items/low_stock.json` and about a dozen others. Pre-existing, unrelated to
vendor exposure, discloses nothing, and every one has a suffix-less twin the
crawl does fetch and search. It is pinned as an exact allowed set, not ignored,
so a NEW exception class still fails.

## Nine vacuous checks, in three shapes

The branch produced nine checks that could not have failed, and three of its
real disclosures were found only after one of them was repaired. The tally, kept
here because it is this branch's count and not a standing figure:

* **Asserted nothing** — the nginx refusal remedy enforced nowhere; a
  write-surface list whose docstring called it derived while the body returned
  eleven literals; an undecodable-PDF branch whose comment said it reported.
* **Guarded on something always true** — `sys.modules` holds
  `rest_framework.serializers` no matter what; a first-party path filter that
  skips every app on a `/tmp` vs `/private/tmp` mismatch and passes having
  inspected nothing.
* **Read nothing** — the crawl 404ing on an untyped router pk; a nested
  serializer over an empty relation; a fixture feeding a signed-in payload to a
  logged-out render.

The two habits that catch all three shapes are in AGENTS.md, because they
generalise: mutation-prove each guard, and give each derived set an
anti-vacuity floor.

## ScanTTY is unaffected — no contract change

Verified against the REAL remote default branch, not a local checkout:
`uid0/scantty` `main` at `ca71ba2a965ec3ae77a8a08e596a0c0a13f1ed40`, SHA
confirmed through the GitHub API. `internal/tui/app.go:139` shows the login
screen unless a token is cached, so every vendor screen is post-login and every
request from one carries `Authorization: Bearer`
(`internal/omsapi/client.go:232,278,399,465`); it never fetches `/media/`. No
key was renamed or removed — they are withheld from callers with no session —
and this was confirmed live: with a Bearer token every endpoint ScanTTY calls
returns 200 with the full vendor payload, including all seven flat compat keys
and the pinned metrics contract.

## Round-by-round history

| Commit | What the round found and fixed |
| --- | --- |
| `88ef3f1` | The instrument, committed red: 20 of 24 failing on 21 paths. |
| `1eddf0b` | The closures themselves, plus `download_card` and the transparency `authentication_classes=[]`. |
| `9c8615e` | `/media/`: four prefixes behind `auth_request`, `config.protected_media` registered unconditionally in place of `if settings.DEBUG: static(...)`. |
| `0815390` | The matrix resolves ENFORCED permissions: 103 `(view, action)` entries were wrong, several with the opposite meaning. Six rows pinned against both the snapshot and a live request. |
| `ba55e43` | The frontend crash this branch introduced: guards spelled `=== null` let `item.unit_cost.toFixed(2)` run on `undefined` and blank the item page. Vendor routes moved behind `RequireAuth`; scan, item and transparency asserted to stay open. |
| `fefb1ed` | Coverage of the crawl measured and bounded; format-suffix routes filled; the `format=None` 500 reported and pinned as an allowed set. |
| `dbef245` | The two write-path disclosures (`scanner/dispatch/`, `log_usage`) and three instrument blind spots. |
| `760e4df` | The transparency page rendering `"N/A"` and `"$NaN"` where the vendor block was; columns dropped rather than blanked; the footer's "ALL financial information" claim reworded. |
| `412aaa8` | The deliberate exclusions reported with reasons, in the matrix. |
| `3395bc2` | Two dispatch tests moved to the authenticated side of the gate they now sit behind. |
| `59a2cd5` | `SupplierChoiceSerializer` delegates to the owner instead of keeping a second spelling of the same rule its docstring already claimed it had dropped. |
| `2a76797` | The authenticated CONTROL user holds no privilege — "behind user auth" means ANY signed-in account, so a staff user would prove something narrower. |
| `307f8b3` | Five more media roots, found by re-deriving on the upload FIELD; `test_upload_field_classification.py` added; nginx assertions moved from grepping to parsing. |
| `e1a78c5` | Item/kit form routes guarded; dead supplier-choice wording dropped. |
| `4bb4011` | Withheld keys gated at the TypeScript type and render boundary, so the compiler makes a reader handle the third state. |
| `e2f22b4`, `12e1ec5` | The media session check: a failed renewal must not fail the refresh, and a refresh slides the session rather than minting a new one. |
| `d489923` | The refused media download gets a remedy that works (`ReauthPage`). |
| `a77de67` | The anonymous write probe was reading the wrong rows. |
| `7e9f4f2` | The nginx refusal page forced to `text/html`. |
