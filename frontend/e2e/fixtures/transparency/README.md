# Recorded transparency payloads

`signed-in.json` and `anonymous.json` are RESPONSE BODIES, recorded verbatim
from `GET /api/reorders/analytics/transparency/` on a real OpenMakerSuite —
`AnalyticsViewSet.transparency` in `backend/reorder_queue/views.py`, on
PostgreSQL, on the branch that introduced `item_supplier_choice` — one request
with a session and one without, over the same rows.

**They are recorded rather than written, and that is the point.** A fixture
written by whoever wrote the page agrees with the page by construction and
cannot contradict it. This pair is what the SERVER actually sends, so the spec
that renders them is asking the real question: what does a reader see?

The rows they carry, and why each is there:

* an ordinary paid order (`ORD-2026-0042`, `actual_cost` 150.25) — the
  everyday row;
* a donated order (`ORD-2026-0043`, `actual_cost` **0.00**) — the recorded
  zero that used to be published as `null` and rendered as a bare "0";
* one purchase order, so the PO block is not empty.

`anonymous.json` is the SAME feed with no session: it carries
`vendor_data_withheld` and no vendor key at all. Re-record both together if the
payload changes; a spec that renders one against the other's vintage proves
nothing.
