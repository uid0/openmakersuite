# Project Instructions

## Workflow Roles (Codex vs Claude Code)

Two coding agents work this repo with split responsibilities:

- **Codex** — acceptance criteria author AND PR/backlog manager. Given a feature request, writes `.criteria/<slug>.md` in the format described in `.criteria/README.md`. Does not modify files under `backend/`, `frontend/`, migrations, or tests itself; however, see "Codex PR authority" below for what it IS authorized to do at PR time.
- **Claude Code** — implementer. Reads `.criteria/*.md` and writes code + tests to satisfy every AC. See `CLAUDE.md` for the full role spec and project conventions.

### Codex PR authority

The operator (uid0) has granted codex standing approval to keep the PR backlog clean. Codex may:

- Bring open PRs up to date with `main` (rebase or merge `main` in), resolving trivial conflicts.
- Resubmit PRs after a rebase or fix to keep them mergeable.
- Close PRs that are obsoleted by other landed work or that the operator clearly won't want.
- Merge PRs that are clean (CI green, no review findings, no operator-blocking comments).

Codex should leave PRs open only when there is an actual problem to surface, and should open GitHub issues only when the operator faces a hard decision (architectural choice, scope/cost tradeoff, requirements ambiguity that can't be resolved from existing docs). Routine status, "FYI" notes, and "task done" markers are not issues — those belong in the bead system or PR comments.

The rest of this file applies to both agents.

## Code Style

- There is a .devcontainer environment for editing and running this application in development mode.
- All changes have to pass pre-commit hooks and the github workflows via the act commands that are present on this system.
- This is for a makerspace -- the default action is open, and while there can be workflows on the admin side of things, consider general requests as unauthenticated unless there is a specific need to either acknowledge or take action on this alert.
- Most calls to actions will be either by wehook push to either discord or integration into slack. Keep that in mind when receiving alerts about supplies being out or areas that may need attention from either the cleaning staff or the logistics/supply team.
- Keep in mind that you may be working in a context local to the developer's machine, or inside the .devcontainer. When writing scripts, assume that the developer's machine is running zsh or bash, and that the .devcontainer runs bash. When running scripts, make sure that you're running inside the devcontainer or on the developer's system.
- The developer does approve some actions manually, so please don't assume that the changes you've asked for are immediately ready for use.
- Place all shell scripts in the ./scripts/ directory

## Architecture

- Follow the repository pattern
- Keep business logic in service layers
- Always provide a reasonable default when creating entries. We want a good out of the box experience for both developers as well as for new users.
- Always write appropriate unit, integration, and end-to-end tests using the native language tools and playwright if needed.
- You don't need to create a markdown file for the things that you've done in the repository. Feel free to summarize those changes in the AGENT's file when they would be beneficial for either a human developer, you, or other development agents in the future.
- Always use black, isort, flake8 for python code to make sure that your code is complianct with the tools that we Lint and CI with.

## Backend

- **Django Version**: Currently using **Django 6.0.7** (see `backend/requirements.txt`; migration headers from 0108 onward record it too)
- **`CheckConstraint` uses `condition=`, not `check=`**: Django 6 removed the
  `check=` keyword. Write `models.CheckConstraint(condition=Q(...), name=...)`,
  matching the existing constraints in `inventory/` and `reorder_queue/`.
- Use `python manage.py startapp` to create new apps within your project
- Keep models in `models.py` and register them in `admin.py` for admin interface
- **A new DRF `@action` fails CI until the permission matrix is refreshed**: `config/tests/test_permission_matrix.py` introspects the live URL conf and fails when `backend/config/api_permission_matrix.yaml` drifts from it. [`docs/API_PERMISSION_MATRIX.md`](docs/API_PERMISSION_MATRIX.md) owns the regeneration command and the table row each endpoint needs.
- Use Django's ORM instead of raw SQL queries
- Avoid N+1 queries with `select_related` and `prefetch_related`:

```python
# Good pattern
users = User.objects.select_related('profile')
posts = Post.objects.prefetch_related('tags')
```

- Use Django forms for validation:

```python
class UserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['username', 'email']
```

- Create custom model managers for common queries:

```python
class ActiveUserManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)
```

- Use Django's built-in authentication system
- Store settings in environment variables and access via `settings.py`

### Adding a routed DRF action

Every URL-routed DRF view — including each `@action` on an existing viewset — is
pinned by a snapshot that `config/tests/test_permission_matrix.py` enforces, so
adding one fails that test until the snapshot and its Markdown row are refreshed
in the same change. The regeneration command and the YAML-wins rule live in
[`docs/API_PERMISSION_MATRIX.md`](docs/API_PERMISSION_MATRIX.md) under "Drift
detection".

A hand-rolled `@action` bypasses its serializer on the way in but usually still
serializes its response through one. Anything you read from `request.data` and
write to a non-text model field must be coerced first — e.g.
`serializers.DateField().to_internal_value(raw)` — and coerced *outside* the
`transaction.atomic()` block, so a bad value is a clean 400 before anything is
written. Skipping this persists the row correctly but leaves a `str` on the
in-memory instance, and the response serializer then 500s *after* the commit,
so a retrying operator accumulates duplicate rows. Worked examples:
`inventory` `generate_work_order` and `reorder_queue` `confirm_order`, pinned by
`inventory/tests/test_generate_work_order_due_date.py` and
`reorder_queue/tests/test_po_confirm_expected_delivery_date.py`.

The companion rule on the same read: a key the client did **not** send is not a
supplied `null`. `request.data.get("field")` collapses the two, so a hand-rolled
action that assigns the result unconditionally silently erases whatever the
record already held whenever a client posts a partial (or empty) body — and the
web app posts no body at all on several of these actions. Gate the write on
`if "field" in request.data`, and where the service needs to tell "unsupplied"
from "explicitly cleared" give it an `UNCHANGED` sentinel default rather than
`None` (`reorder_queue.services.purchase_orders.confirm_order`). The convention
is stated in `ReorderRequestViewSet.mark_ordered`'s docstring and pinned by
`reorder_queue/tests/test_po_confirm_preserves_expected_delivery_date.py`.

### Which supplier an item is bought from: one derivation, orderable only

`inventory.services.supplier_selection` is the ONE answer to "which supplier for
this item". Everything else reads it — `InventoryItem.primary_item_supplier` and
the seven flat compat properties, `item_metrics` (the pinned ScanTTY contract),
the order pad and the PO-building screens. Do not re-derive it: three copies of
`ORDER BY -is_primary, unit_cost` had already drifted apart before op-2rsp
collapsed them.

The forecasts (`component_forecast`, `demand_forecast_engine`) still resolve
their own lead time and are NOT on this derivation. That is deliberate for now:
routing them through it moves a reorder point and therefore a flag, and op-2rsp
ships no flag change. They belong to the deferred piece below.

The rule is three things, in this order:

1. **Eligibility.** Only orderable links are candidates — a link that is not
   `is_active`, or that is `is_discontinued`, is never the answer. That includes
   one an operator flagged primary, because `mark_discontinued` deliberately
   leaves `is_primary` set.
2. **The gate.** An orderable link flagged primary wins OUTRIGHT and is never
   scored. A flagged primary is not a term in a sum — any weight can be outbid,
   and then the operator's explicit choice is merely expensive rather than
   binding. Do not "fix" a selection problem by adjusting a bonus.
3. **The score.** Only when nothing orderable is flagged does `score_candidate`
   rank the candidates on cost and lead time.

Ask `select_supplier` / `select_suppliers_for` when you must explain yourself to
an operator: they separate `NO_SUPPLIERS` from `NONE_ORDERABLE`, which are
different facts needing different actions, and flag when the operator's own
choice was the row that got skipped. `primary_item_supplier` is the same answer
with the reason dropped.

Filtering happens in Python, on `item_suppliers.all()`, so the prefetch cache
still serves it — a fresh `.filter()` reintroduces the per-row N+1 that #882
removed and that `docs/API_LIST_CONTRACT.md` bounds in CI. The cost yardstick
(`average_orderable_unit_cost`) is computed in Python for the same reason.

**The scoring weights are a product decision, not an implementation detail.**
They came from the rival rule this replaced and are pinned as they stand;
`inventory/tests/test_supplier_scoring.py` asserts each one and names five
places the judgement is questionable (`REPORTED, NOT FIXED`) — most importantly
that an unpriced supplier can never beat a priced one, because a missing price
scores like a bad price. Two of the five are the same falsy-guard mistake: a
`unit_cost` of 0 and an `average_lead_time` of 0 both read as "unknown", so the
best possible price and the best possible lead time are each graded as the
worst. Retuning any of them needs a captain decision, and the tests will fail
until it is deliberate.

`PurchaseOrderViewSet._find_best_supplier` is now a thin delegation, kept for its
call site's readability. It has no rule of its own.

Aggregates that value stock rather than choose a vendor (`lowest_unit_cost`,
`total_value`, the report averages) deliberately still read every link.

`InventoryItem.current_cases` is EXCLUDED from this supplier derivation.
Deriving from the READERS OF A SYMBOL is not the same as deriving from the
QUESTION BEING ASKED: it reads the helper but asks a different question — how
many units are in a box on the shelf — which has nothing to do with who we buy
from. It resolves its pack size from ANY link, orderable or not, because a dead
vendor's recorded pack size still describes the box already on the shelf, and
routing it through the orderability filter suppressed a low-stock alert.

**Deferred and filed: one named derivation for pack size, the way "which
supplier" got one.** It still has several readers with no single owner —
`current_cases` (any link) versus `quantity_per_package`, `item_metrics`'s
`case_size` and `bridge_case_reorder_to_packaging` (the supplier helper) — so
they can disagree on an item whose links differ. Do that work rather than
rediscovering the split.

### The alert-suppression class: IDENTIFIED, not fixed

A value made honestly `None` gets collapsed by downstream arithmetic or a
fallback into a confident, OPTIMISTIC answer — which inverts a boolean and
suppresses an alert on exactly the item that most needs one. THREE derived
instances, all made reachable by op-2rsp's orderability filter:

1. `InventoryItem.current_cases` — a `None` supplier falls through to "1 unit
   per package", so raw base units read as a case count.
2. `component_forecast`'s `reorder_point` — `lead_time_days or 0` computes a
   horizon at a zero-day wait.
3. `demand_forecast_engine.forecast_item_by_interval` — the same collapse in
   the demand-forecast report and the nightly reorder digest.

**The fix was attempted across four review rounds and REVERTED wholesale.** Do
not simply retry it. Each round's fix opened a new site: making these values
null reached untyped frontend consumers (`current_cases.toFixed(1)` on the scan
and item-detail pages), removed a low-stock alert that worked before, and
re-collapsed `NO_SUPPLIERS` with `NONE_ORDERABLE` at one site while narrowing
them at another. The blast radius of making a previously-total value nullable —
across untyped consumers and boolean comparisons — is larger than the
inconsistency it removes. op-2rsp therefore ships the supplier derivation ONLY,
and changes no reorder flag anywhere.

The redo is one coherent piece, not three patches: derive and retype EVERY
consumer first, then represent unknown pack size / lead time / reorder point
honestly, keeping `NO_SUPPLIERS` (a data gap) and `NONE_ORDERABLE` (unbuyable)
distinct throughout — flagging the data-gap population regardless of stock
floods the surface until people ignore it, which suppresses alerts too.

Each instance was found late, and by the same mistake each time: COUNTING WHAT
HAD BEEN SEEN INSTEAD OF DERIVING THE SET OF CONSUMERS — in the very task that
exists to end that failure, and a derivation done because the count was
challenged is still a derivation done late. The same root shows up in the
scoring's falsy guards, where a missing or zero value reads as "absent" or
"bad" rather than "unknown", and in `reorder_queue/views.py`'s `unit_cost or 0`.

### The pre-send boundary: when a PO is still the shop's own document

`PurchaseOrder.PRE_SUPPLIER_STATUSES` is the ONE definition of "the supplier has
not seen this order", and it sits beside `RECEIVABLE_STATUSES` /
`IN_RECEIVING_STATUSES` on the model. Both line-set guards read it —
`services.line_entry.assert_addable` and `assert_deletable` — and the API serves
the answer rather than making clients derive it: `can_delete_items` on the order
serializer (beside `can_receive`, same discipline) and `can_add_items` on the
item-lookup payload. Gate on the set; never compare to `Status.DRAFT` by name,
or a second pre-send state becomes a hunt through the comparisons.

It is what separates the two line-removal verbs, which are NOT variants of each
other: while the order is pre-send a mistaken line is a typo and is DELETED
outright (no reason, irreversible, no ghost); once the supplier holds a copy it
can only be VOIDED (reason required, struck off, kept on the record). The web
page offers exactly one of the two, chosen off `can_delete_items`.

The delete path REFUSES a line carrying `quantity_received > 0`, with a 400 and
a message naming the recorded quantity. `DRAFT` is initial-only — nothing in
the codebase writes an order back to it — so that branch should be unreachable,
and it is kept anyway: an impossibility argument is only true while every
future change re-verifies it, whereas a guard holds without anyone re-verifying
anything, and what it protects against is destroying goods a receipt says
arrived. Prefer the guard to the argument wherever the argument is about what
some other part of the codebase will never do.

Outside the pre-send set sit two different reasons, and `assert_deletable` says
whichever is true: "the supplier already has this line, void it instead" and
"this order is closed and never went out, start a new one". The split is derived
from the two frozensets — the closed case is *outside `PRE_SUPPLIER_STATUSES`
and outside `IN_RECEIVING_STATUSES`*, i.e. the terminal statuses, never a typed
list of labels — with `sent_at` read only as corroboration. Do NOT key such a
split on `sent_at` alone: `PurchaseOrderAdmin.mark_as_sent` moves a queryset to
`SENT` with one `update()` and stamps `sent_by` but not `sent_at`, so an order
can be live with its supplier and hold no stamp. A refusal is only legitimate
when the operator can act on it, and a refusal that misstates why is worse than
a bare one.

One thing found here and deliberately NOT changed, routed to follow-up instead
(the list-filter defect found alongside it — `get_queryset` dropping a draft
off the list once its only line was gone — has since been fixed, and the next
section owns that rule):

- `PurchaseOrderAdmin.mark_as_sent` never stamps `sent_at`, which also leaves
  `days_since_ordered` reading 0 for orders sent that way. Changing it alters
  existing admin behaviour, so instead nothing in this change depends on that
  stamp being written.

### Two kinds of empty purchase order, and when emptiness hides one

The LIST action hides an order **emptied by voiding** that is outside
`PurchaseOrder.PRE_SUPPLIER_STATUSES`, and nothing else. An order with **no
line items at all** is listed in every status.

No status NAME appears in the condition: the pre-send clause reads the
`PRE_SUPPLIER_STATUSES` frozenset, the same one `assert_addable` /
`assert_deletable` / `get_can_delete_items` read, so a second pre-send status
is one edit to that frozenset and none of it is here.

`PurchaseOrderViewSet.get_queryset` owns the derivation;
`reorder_queue/tests/test_po_list_emptiness.py` owns the behaviour, crossing
status × line-population rather than enumerating cases in prose.

Two related questions are known and filed separately: the `VOIDED`-order
display inconsistency on staff's list, and `void_item` carrying no status gate.

### Purchase-order line settlement

"Is receiving finished with this line?" is defined once, on
`PurchaseOrderItem.is_settled`, and nowhere else. Six defects had come from
code answering it with a predicate of its own — the last one from another app
entirely — so `backend/reorder_queue/settlement_sites.py` derives the whole set
of sites from that property (it walks it with `ast` to the model fields, then
sweeps `backend/` and `frontend/src`) and fails when one bypasses it. Run it for
the report, `--sites` for every reader:

```
python3 backend/reorder_queue/settlement_sites.py
```

It runs as `reorder_queue/tests/test_settlement_sites.py` in Backend Tests and
as a step in Frontend Lint, so a frontend-only PR is covered too. If it flags
your change, route the site through the derivation rather than widening the
guard: `PurchaseOrderItem.receipt_state` / `is_settled` in Python,
`PurchaseOrderItem.q_settled()` / `objects.outstanding()` /
`objects.with_receipt_state()` in the ORM, and `receipt_state` / `is_settled`
off the API on the frontend. Anything that can settle a line must reach
`services.refresh_receipt_status` before it returns.

**Where the refresh actually lives.** Saving or deleting a LINE re-derives its
order on its own — `reorder_queue/settlement_signals.py` hangs off
`PurchaseOrderItem`'s `post_save` / `post_delete`, and `pre_save` captures the
order a line is LEAVING so a reparent re-derives both ends. No admin hook owns
that re-derivation any more — the admin still opens `settlement_batch()` so a
formset save asks once, and `ReceiptStatusFilter` still *reads* settlement
through `with_receipt_state()`, but neither decides it. A hook used to: the
change form, then the inline formset, then row delete, then bulk delete, then
reparenting, each closed by adding another method name to a list, which is the
mistake this section exists to stop.

**The delete signal carries a second, non-settlement obligation.** A line
DELETE also re-rolls `PurchaseOrder.estimated_total`, which is a STORED sum
frozen from the line costs — voided lines stay in it (`effective_estimated_total`
subtracts them at read time), but a deleted line is subtracted by nobody, so
without this the order reports money for a line that no longer exists. It rides
`post_delete` rather than living in the delete endpoint because the Django
admin's row / inline / bulk deletes are three more routes that remove a line,
and they were already overstating the total before that endpoint existed.

The rule is "a line's cost LEFT the order", so it covers the admin change
form's REPARENT too — moving a line to another order removes its cost from the
one it left exactly as a delete does, and leaves the one it joined
understating. That case re-rolls both orders from the post_save receiver.

Ordinary line SAVES stay excluded, and the boundary is narrower than it sounds:
the API's own `add_line_item` / `update_item` re-roll on their own path, but the
admin does NOT — neither `save_model` nor `save_formset` calls
`recalculate_estimated_total` — so an admin quantity edit, reprice or inline add
still leaves the stored total stale. It is left open deliberately, because the
signal only compares fields inside the settlement closure and
`unit_cost_ordered` is not one of them, so closing half of it would mean an
invariant documented as held and not held. `oms-derived-totals-beyond-settlement`
(order-level figures computed from lines that only some line-writing paths
re-derive) is STILL NEEDED for the rest; removal is covered, editing is not.

When you state a rule like this one in a docstring, read it back against every
route that satisfies its antecedent — the reparent gap was a stated rule the
code did not honour, and it is the worked instance to build that issue on.

**What the signal does NOT cover, and why the guard still has a job:**
querysets fire no per-object save signal. `PurchaseOrderItem.objects.filter(...)
.update(...)` and `bulk_update` write settlement columns with nothing hearing
about it, so those paths must call `services.refresh_receipt_status` themselves
— `services.purchase_orders.void_po` is the live example. Ordinary
`queryset.delete()` IS covered (it fans `post_delete` out per row), but a FAST
DELETE is not: a collector that can drop rows with one `_raw_delete` sends no
signal, and `_raw_delete` called directly never does.

Three properties the routing holds, all pinned by tests rather than asserted:
receiving a twenty-line order re-derives the order ONCE (`settlement_batch()`
coalesces inside the caller's unit of work, never on `transaction.on_commit` —
endpoints serialize `purchase_order.status` into the response and ScanTTY reads
it); a save that moved no settlement field and did not move the line to another
order re-derives NOTHING, so editing a note leaves an operator's chosen status
alone; and a refresh cannot re-enter its own signal.

Do not read a clean run as "there is nothing left". The scan prints the write
shapes it can and cannot see on every run; that list is the honest boundary and
it has grown twice already.

**A file it could not read is not a file it cleared.** The scan exits non-zero
for a module it cannot parse or decode, not just for a site that bypasses the
derivation, and names each one under `NOT scanned`. It used to `continue` past
them, so a run in which N modules failed to parse still printed `Scanned:
backend, frontend/src` and a clean verdict — which is why Frontend Lint now sets
up Python 3.14 before running it. Run the scan under the version the backend
targets; a partial sweep says so in its summary rather than passing for a whole
one.

**A custom `QuerySet` method can become a `Manager` method by accident.**
`BaseManager._get_queryset_methods` copies every public queryset method onto the
manager EXCEPT those marked `queryset_only`, and that marker does not survive an
override. `PurchaseOrderItemQuerySet.delete` shipped without re-setting it,
which made `PurchaseOrderItem.objects.delete()` a real, callable method that
takes no filter and empties the table. Any override of a method Django withholds
needs `<name>.queryset_only = True` after its `def`, the way `QuerySet`'s own
does. Enforced across every model in the repo — the check derives the withheld
set from `QuerySet` and `Manager` rather than naming `delete`, so a second
queryset class in any app is already covered.

### Django upgrade history

The backend now runs **Django 6.0.7**. The notes below cover the earlier 4.2 -> 5.1
upgrade and are retained for context; anything they say about 5.1 being the
current version is superseded by the 6.0.7 pin in `backend/requirements.txt`.

Django 6 changes that affect code in this repo:

- `CheckConstraint(check=...)` is gone -- use `condition=`.
- Constraint validation runs in `Model.full_clean()` by default; pass
  `validate_constraints=False` where a model deliberately leaves enforcement to
  the database (see `inventory/models/kit.py`).

The project was successfully upgraded from Django 4.2.27 to Django 5.1.14, then to 5.1.15 for security. Key points:

- **All tests pass**: 389 passed, 2 skipped with Django 5.1.15
- **No breaking changes**: All custom admin filters (`DeliveryPerformanceFilter`, `ReceiptStatusFilter` in `reorder_queue/admin.py`) work correctly
- **Package compatibility**:
  - `django-passkey-auth==0.2.0` works with Django 5.1 (no explicit support but tested and functional)
  - All third-party packages updated to Django 5.1-compatible versions
- **No deprecation warnings**: Clean upgrade with no deprecated features in use
- **Database**: PostgreSQL 15 meets Django 5.1 requirements (13+)
- **Migrations**: All migrations run cleanly, no issues detected

Updated packages:

- Django: 4.2.27 → 5.1.14 → 5.1.15 (security fix for XML deserialization DoS vulnerability)
- djangorestframework: 3.15.2 → 3.16.1
- django-cors-headers: 4.6.0 → 4.9.0
- django-redis: 5.4.0 → 6.0.0
- django-celery-results: 2.5.1 → 2.6.0
- drf-spectacular: 0.27.2 → 0.29.0

## Frontend

- Use functional components with hooks
- Follow a consistent folder structure (components, screens, navigation, services, hooks, utils)
- Use React Navigation for screen navigation
- Use StyleSheet for styling instead of inline styles
- Use FlatList for rendering lists instead of map + ScrollView
- Use custom hooks for reusable logic
- Implement proper error boundaries and loading states
- Optimize images and assets for mobile performance
- **A rejected field never reaches the operator through `extractErrorMessage`**: the
  error envelope ([`docs/API_ERROR_CONTRACT.md`](docs/API_ERROR_CONTRACT.md) owns its
  shape) keeps the field map in `error.details` behind a flat "One or more fields failed
  validation." message, and `src/utils/extractErrorMessage.ts` returns only that message.
  A form that has to name the rejected field reads `details` itself —
  `src/utils/supplierRelationships.ts` is the pattern.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
