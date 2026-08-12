# Django Upgrade Baseline

## Context
The django-upgrade pre-commit hook currently rewrites the same four legacy Django idioms whenever agents run it, leaving unrelated worktrees dirty. This chore establishes a dedicated baseline for those deterministic rewrites so later work does not repeatedly rediscover them.

## Scope
- In: The django-upgrade rewrites in `backend/membership/views.py`, `backend/notifications/device_login.py`, `backend/preventive_maintenance/admin.py`, and `backend/project_storage/admin.py`; preserving the existing client IP, user-agent, and Django admin column-label behavior; targeted verification for the touched backend apps.
- Out: Popping or applying any git stash; editing any path outside the four implementation files; changing models, migrations, tests, frontend code, settings, permissions, or business behavior; making unrelated main-branch pre-commit failures green; using `pre-commit run --all-files` as the acceptance gate for this chore.

## Criteria

### AC-1: Implementation diff is limited
- **Given** the criteria commit is the starting point for implementation
- **When** maintainers inspect the implementation diff after that commit
- **Then** the only changed non-criteria paths are `backend/membership/views.py`, `backend/notifications/device_login.py`, `backend/preventive_maintenance/admin.py`, and `backend/project_storage/admin.py`

### AC-2: Django upgrade hook is clean
- **Given** the four django-upgrade rewrites have been committed on the chore branch
- **When** `pre-commit run django-upgrade --all-files` is executed
- **Then** the command exits successfully and a following `git status --short` shows no uncommitted django-upgrade rewrites

### AC-3: Invite redemption keeps forwarded IP behavior
- **Given** an anonymous invite redemption request includes `X-Forwarded-For: 203.0.113.10, 198.51.100.2`
- **When** the invite code is redeemed successfully
- **Then** the redeemed invite records `redeemed_ip` as `203.0.113.10`

### AC-4: Device login keeps request header behavior
- **Given** a staff user's new-device login request includes `X-Forwarded-For` and `User-Agent` HTTP headers
- **When** device login tracking records the known-device row and notification metadata
- **Then** the recorded IP is the first forwarded IP value and the recorded user agent matches the incoming user-agent header

### AC-5: Admin display labels are preserved
- **Given** Django admin loads the preventive maintenance and project storage model admins
- **When** the list display metadata is inspected for the changed display callables
- **Then** preventive maintenance still labels `_status` as `Status` and `_days_since` as `Last service`, and project storage still labels `status_display` as `Status`

### AC-6: Targeted backend tests pass
- **Given** the branch contains only the scoped django-upgrade implementation changes
- **When** `cd backend && pytest membership notifications preventive_maintenance project_storage` is executed
- **Then** the targeted backend tests for the four touched apps pass

## Verification Commands
- `pre-commit run django-upgrade --all-files`
- `cd backend && pytest membership notifications preventive_maintenance project_storage`
- `cd backend && python manage.py makemigrations --check`
- `cd backend && python manage.py check_permission_matrix`
- `cd frontend && npm test`
