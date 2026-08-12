# Django Upgrade Baseline

## Context
The django-upgrade pre-commit hook currently rewrites the same four legacy Django idioms whenever agents run it, leaving unrelated worktrees dirty. This chore establishes a dedicated baseline for those deterministic rewrites so later work does not repeatedly rediscover them. The baseline must prove the rewritten request-header and admin-display behavior with focused regression tests, not just diff inspection.

## Scope
- In: The django-upgrade rewrites in `backend/membership/views.py`, `backend/notifications/device_login.py`, `backend/preventive_maintenance/admin.py`, and `backend/project_storage/admin.py`; focused backend tests that prove invite redemption forwarded IP behavior, device-login request-header behavior, and preserved admin display labels; targeted verification for the touched backend apps.
- Out: Popping or applying any git stash; editing implementation paths outside the four django-upgrade files; changing models, migrations, frontend code, settings, permissions, or business behavior; broad or unrelated test rewrites; making unrelated main-branch pre-commit failures green; using `pre-commit run --all-files` or frontend tests as acceptance gates for this chore.

## Criteria

### AC-1: Implementation diff is limited
- **Given** maintainers inspect the completed branch diff against `main`
- **When** they ignore the criteria file itself
- **Then** changed non-criteria paths are limited to `backend/membership/views.py`, `backend/notifications/device_login.py`, `backend/preventive_maintenance/admin.py`, `backend/project_storage/admin.py`, `backend/membership/tests/test_invite_codes.py`, `backend/notifications/tests.py`, and focused admin metadata tests under `backend/preventive_maintenance/tests/` and `backend/project_storage/tests/`

### AC-2: Django upgrade hook is clean
- **Given** the four django-upgrade rewrites have been committed on the chore branch
- **When** `pre-commit run django-upgrade --all-files` is executed
- **Then** the command exits successfully and a following `git status --short` shows no uncommitted django-upgrade rewrites

### AC-3: Invite redemption keeps forwarded IP behavior
- **Given** an anonymous client posts to `reverse("invite-redeem")` with `HTTP_X_FORWARDED_FOR="203.0.113.10, 198.51.100.2"`
- **When** the invite code is redeemed successfully
- **Then** the saved `InviteCode` row records `redeemed_ip` as `203.0.113.10`

### AC-4: Device login keeps request header behavior
- **Given** a staff user's new-device login request includes `X-Forwarded-For: 203.0.113.10, 198.51.100.2` and `User-Agent: OpenMakerSuite Test Browser`
- **When** device login tracking records the known-device row and account-security notification
- **Then** the `KnownDevice` row stores `203.0.113.10` and `OpenMakerSuite Test Browser`, and the notification metadata contains the same `ip` and `ua` values

### AC-5: Admin display labels are preserved
- **Given** Django admin loads the preventive maintenance and project storage model admins
- **When** the list display metadata is inspected for the changed display callables
- **Then** preventive maintenance still labels `_status` as `Status` and `_days_since` as `Last service`, and project storage still labels `status_display` as `Status`

### AC-6: Targeted backend tests pass
- **Given** the branch contains only the scoped django-upgrade implementation changes and focused regression tests
- **When** `cd backend && pytest membership notifications preventive_maintenance project_storage` is executed in a PostgreSQL-backed environment
- **Then** the targeted backend tests for the four touched apps pass

## Verification Commands
- `pre-commit run django-upgrade --all-files`
- `cd backend && pytest membership notifications preventive_maintenance project_storage`
- `cd backend && python manage.py makemigrations --check`
- `cd backend && python manage.py check_permission_matrix`
