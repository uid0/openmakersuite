# Project Instructions

## Workflow Roles (Codex vs Claude Code)

Two coding agents work this repo with split responsibilities:

- **Codex** — acceptance criteria author. Given a feature request, writes `.criteria/<slug>.md` in the format described in `.criteria/README.md`. Does not modify files under `backend/`, `frontend/`, migrations, or tests.
- **Claude Code** — implementer. Reads `.criteria/*.md` and writes code + tests to satisfy every AC. See `CLAUDE.md` for the full role spec and project conventions.

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

- **Django Version**: Currently using Django 5.1.15 (upgraded from 4.2.27 in December 2025, security update to 5.1.15 in January 2026)
- Use `python manage.py startapp` to create new apps within your project
- Keep models in `models.py` and register them in `admin.py` for admin interface
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

### Django 5.1 Upgrade Notes (December 2025)

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
