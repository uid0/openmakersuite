# Reactive workflow transitions

OpenMakerSuite is a React application, and operators expect it to feel like
one. After a routine action — marking a reorder request received, marking a
purchase order delivered, resolving a location problem, transitioning a work
order — the page should update **in place** from the mutation response.
A full re-fetch that flips the page back through its initial loading
placeholder is a regression, not a refresh.

This document is the contract for that behavior. New mutation-driven UI must
follow it, and the existing refresh-y inventory below should be migrated
toward it.

The supporting acceptance criteria are in
[`.criteria/reactive-workflow-transitions.md`](../.criteria/reactive-workflow-transitions.md).

---

## The reactive mutation standard

When the user submits a successful operational mutation:

1. **Patch visible state from the mutation response.** Backend mutation
   endpoints return the full updated representation. Replace the affected
   row/card/panel/detail object in local state from the response. Do not
   call the initial loader as a "refresh."
2. **Preserve operator context.** Do not reset the filter bar, table sort,
   scroll position, selected row, or expanded sections. The operator's eye
   stays where it was when they clicked.
3. **Scope the pending state.** Only the affected control / row / section
   should look busy. Unrelated controls remain usable where safe. The
   submitting control is disabled until the request resolves so a double
   click cannot fire the mutation twice.
4. **Reserve full blocking reloads for explicit reasons.** The page-level
   `loading` state is for the **initial** fetch only, or for an explicit
   "I am switching to a different resource" action. A successful mutation
   never sets `loading = true`.
5. **Failure preserves context.** A 4xx/5xx response leaves the page,
   filters, scroll position, and user-entered text intact. The error is
   shown as a scoped inline message or notification; the user can retry
   without navigating away or reloading.
6. **Background reconciliation is non-disruptive.** If, after the local
   patch, a follow-up fetch is needed to reconcile derived/computed fields
   (e.g. totals that the mutation endpoint doesn't return), it must not
   flip the page back into its initial loading placeholder, must not clear
   selection / filter / scroll, and must not overwrite a newer local
   mutation result with stale data (compare `updated_at` or otherwise
   merge defensively).

These six points are the **reactive mutation contract**. Tests must assert
this contract — see "Testing the contract" below.

---

## Choosing a strategy

```
                                    ┌──────────────────────────┐
mutation endpoint returns the full  │  PATCH FROM RESPONSE     │
updated resource (most cases)  ───► │  setOrder(resp.data)     │
                                    └──────────────────────────┘

mutation endpoint returns a delta   ┌──────────────────────────┐
or status code only ──────────────► │  PATCH FROM KNOWN STATE  │
                                    │  setItem({...item, ...}) │
                                    └──────────────────────────┘

mutation has side effects we can't  ┌──────────────────────────┐
predict (totals across many rows,   │  PATCH OPTIMISTICALLY    │
inventory adjustments) ───────────► │  then non-disruptive     │
                                    │  background reconcile    │
                                    └──────────────────────────┘
```

For the third case, the reconcile must NOT call the initial `setLoading(true)`
loader. It runs as a quiet background fetch (e.g. `silentReload()` that only
sets a small `reconciling` flag, not the page-blocking `loading` flag), and
its result must not clobber a newer local mutation made while it was in
flight.

---

## Reference implementation: purchase receiving

These two pages are the proving path for AC-3 of
`reactive-workflow-transitions.md`.

### Reorder triage table (`AdminDashboard.tsx`)

Behavior:

- Each row in the requests table carries its own pending flag
  (`pendingRowIds`), so the user can see exactly which row is busy.
- `mark_received`, `approve`, `mark_ordered`, `cancel`, and `update_tracking`
  all patch the row from the API response via
  `applyRequestUpdate(setRequests, updated)`. The filter bar, scroll
  position, "View by supplier" panel, and "Assets Not Checked In" panel
  are not touched.
- The row's action buttons are disabled while pending, so a double click
  cannot resubmit the same mutation.
- On failure the row stays exactly as it was; the error surfaces as a
  notification and the operator can retry without reloading.

### Purchase order detail (`PurchaseOrderPage.tsx`)

Behavior:

- `mark-delivered` patches the order from the API response via `setOrder`.
  The page does not flip back to its initial "Loading purchase order…"
  state. The user's typed receipt notes (carrier, tracking) remain on
  screen only as long as the panel is open and clear once the mutation
  succeeds.
- The "Mark as Delivered" submit button is disabled while in flight.
- On failure the delivery panel stays open with its inputs intact so the
  operator can correct and retry.

---

## Reference implementation (non-purchasing): location problem resolution

This is the proving path for AC-4. `LocationProblemDetailPage.tsx`:

- `resolve` (mark resolved / closed), `promote-standard`, and
  `promote-third-party` all patch the visible problem from the mutation
  response. No follow-up GET is needed because the endpoint returns the
  full updated `LocationProblem`.
- Action buttons are disabled while in flight.
- On failure, the resolve / promote panels remain visible with the
  user's resolution notes / chosen vendor still in their fields.

---

## Refresh-y workflow inventory

This inventory is the AC-1 deliverable. Each row is a mutation path that
currently calls a page/table/detail loader after success, ordered by
operator impact (highest first). New work should migrate rows to the
reactive standard above; until then they remain "refresh-y."

Each row lists the file, the mutation, and the loader being called.

### High impact — operator does this all day

| Status | File | Mutation | Currently calls |
| --- | --- | --- | --- |
| ✅ migrated | `pages/AdminDashboard.tsx` | reorder approve / mark ordered / mark received / cancel / update tracking | (now patches row from response) |
| ✅ migrated | `pages/PurchaseOrderPage.tsx` | `mark-delivered` | (now patches order from response) |
| ✅ migrated | `pages/LocationProblemDetailPage.tsx` | resolve / promote-standard / promote-third-party | (now patches problem from response) |
| refresh-y | `pages/WorkOrderPage.tsx` | work-order status transition + save | `loadWorkOrder()` |
| refresh-y | `pages/ChecklistCompletionPage.tsx` | submit checklist item | `loadData()` |
| refresh-y | `pages/AssetDetailPage.tsx` | maintenance action, check-in, transfer | `loadAssetDetails()` |
| refresh-y | `pages/AssetScanPage.tsx` | report problem, log maintenance | `loadAsset()` |

### Medium impact — recurring administrative work

| Status | File | Mutation | Currently calls |
| --- | --- | --- | --- |
| refresh-y | `pages/PurchaseOrderPage.tsx` | line-cost edit, shipment-date edit, void item, void PO, metadata edit, attachment upload/delete | `loadOrder()` |
| refresh-y | `pages/InventoryReconciliationPage.tsx` | adjust counts | `load()` |
| refresh-y | `pages/InventoryListPage.tsx` | bulk row mutation | `loadData()` |
| refresh-y | `pages/CategoryListPage.tsx` | save / delete category | `loadCategories()` |
| refresh-y | `pages/LocationListPage.tsx` | save / delete location | `loadLocations()` |
| refresh-y | `pages/LocationDetailPage.tsx` | resolve embedded problem | `loadLocation()` |
| refresh-y | `pages/SIGDashboard.tsx` | SIG status transitions | `loadSIGs()` |
| refresh-y | `pages/WebhookListPage.tsx` | enable / disable / test webhook | `loadWebhooks()` |
| refresh-y | `pages/ElectricalCircuitsPage.tsx` | add / edit / delete circuit/breaker/outlet | `reload()` |
| refresh-y | `pages/UserProfilePage.tsx` | save preferences | `loadProfile()` |

### Lower impact — narrow surfaces

| Status | File | Mutation | Currently calls |
| --- | --- | --- | --- |
| refresh-y | `pages/InventoryItemDetailPage.tsx` | regenerate QR | `loadData()` |
| refresh-y | `pages/AssetReportPage.tsx` | filter change | `loadAssetsByStatus()` |
| refresh-y | `pages/DonationItemScanPage.tsx` | donate / pickup | `loadItem()` |
| refresh-y | `pages/FixtureScanPage.tsx` | fixture refill / problem | `loadFixture()` |
| refresh-y | `pages/LocationScanPage.tsx` | complete task | `loadTasks()` |

**Migration policy:** Migrate high-impact rows first; medium and low impact
rows migrate opportunistically as nearby work touches them. Each migration
should be testable against the contract below.

---

## Testing the contract

For every reactive mutation, the test suite should assert four invariants
(this is AC-8):

1. **Local UI updates from the response.** After the mutation resolves,
   the affected row / panel reflects the response payload without a
   second GET.
2. **No full loading placeholder.** During and after submit, the page's
   initial loader text/element (e.g. "Loading requests…", "Loading
   purchase order…", "Loading problem…") must not appear. The existing
   table/page contents stay visible.
3. **Duplicate submit is prevented.** While the mutation is in flight,
   clicking the submitting control again does not call the API a second
   time (the button is disabled, or the handler short-circuits on a
   pending flag).
4. **Failure leaves UI intact.** If the API rejects, the affected row /
   panel is still rendered with its prior state, and the submitting
   control is re-enabled so the user can retry.

Both the unit tests for each page and the cross-cutting documentation
should reference these four points so future contributors don't drift.

---

## What this standard explicitly does not require

To keep the contract small and shippable:

- It does **not** require WebSockets / SSE / push.
- It does **not** require a global client-state library (Redux, Zustand,
  TanStack Query, etc.). Local `useState` is fine; introducing a shared
  cache is a separate design decision.
- It does **not** change backend business rules or permission policy.
- It does **not** mean every refresh in the codebase has to migrate in
  one PR. The inventory above is the punch list; the migration is
  incremental.
