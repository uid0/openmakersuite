# Incident runbook: conflicting migration leaves broke every deploy

> Tracking bead: **op-yfrf** (P1). Incident date: **2026-07-08**.
> Fix that unblocked deploy: **PR #864** (`0080_merge_20260708_1504`).
> CI guard added by this bead: `manage.py check_migration_conflicts` +
> the "Check for conflicting migration leaves" step in `.github/workflows/ci.yml`.

## Symptom

Every production deploy failed. The backend container aborted at startup
during `migrate`:

```
django.db.migrations.exceptions.CommandError:
  Conflicting migrations detected; multiple leaf nodes in the migration
  graph: (0079_alter_workordersubmission_source,
  0079_assetmeter_assetmeterreading_and_more in inventory).
To fix them run 'python manage.py makemigrations --merge'
```

CI had been green on both PRs. The conflict only appeared once both were on
`main`, so it was invisible until the deploy pipeline ran `migrate`.

## Root cause

Two PRs each branched a **new `0079` inventory migration off the same parent**
(`0078_supplier_ordering_adapter`) and both merged:

- #860 (Asset Meter): `inventory/0079_assetmeter_assetmeterreading_and_more`
- #862 (OMR scan reader): `inventory/0079_alter_workordersubmission_source`

That left **two leaf nodes** in the `inventory` migration graph. Django refuses
to build a migration plan when an app has more than one leaf — `migrate` (and
any command that loads the graph) aborts.

Why CI never caught it: each PR, evaluated on its own, had exactly **one** `0079`
leaf (its own). A single-leaf graph is valid, so `makemigrations --check` passed
on each PR. The conflict is a property of the *pair*, which no CI run ever saw
because neither PR was rebased onto a `main` that already contained the other's
`0079` before it merged.

## Mitigations in place (op-yfrf fix)

1. **Explicit CI gate.** `backend/config/management/commands/check_migration_conflicts.py`
   loads the on-disk migration graph (`MigrationLoader(None)`, no database) and
   exits non-zero if any app has multiple leaf nodes. The `backend-tests` job
   runs it as the **"Check for conflicting migration leaves"** step, and
   `config/tests/test_migration_conflicts.py` runs the same check inside the
   suite (so `cd backend && pytest` catches a committed conflict locally too).

   Run it by hand any time:

   ```
   cd backend && python manage.py check_migration_conflicts
   ```

2. **Branch protection — action required (Ian).** The CI gate only sees the
   conflict when it evaluates a tree containing **both** leaves. GitHub runs PR
   CI against the PR-merged-into-base ref, but a *stale* PR is merged against an
   old base, so the second `0079` can still slip through. Turn on, for `main`:

   > Settings → Branches → branch protection rule for `main` →
   > **"Require branches to be up to date before merging"**

   With that on, a PR that adds `0079` must be rebased onto a `main` that already
   has the other `0079` (and re-run CI) before its merge button unlocks — at
   which point the graph has both leaves and the CI step fails, as intended.

## If it happens again

Rebase the offending branch onto the latest `main`, then add a merge migration
and commit it:

```
cd backend
python manage.py makemigrations --merge   # writes an inventory/00NN_merge_*.py
git add inventory/migrations/00NN_merge_*.py
```

Re-run `python manage.py check_migration_conflicts` — it should print
`OK — every app's migration graph has a single leaf node.`
