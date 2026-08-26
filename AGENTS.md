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

### Purchase-order line settlement

"Is receiving finished with this line?" is defined once, on
`PurchaseOrderItem.is_settled`, and nowhere else. Seven defects have come from
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

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
