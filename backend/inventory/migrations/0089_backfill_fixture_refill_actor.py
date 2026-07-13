# Data migration: backfill the new actor FKs on FixtureRefillRequest (#888).
#
# Runs AFTER 0088 adds ``requested_user`` / ``resolved_user``. For each existing
# row we try to recover the auth link from the legacy free-text ``requested_by``
# / ``resolved_by`` string by matching it against ``User.username`` first, then
# ``User.handle`` — but ONLY when the match is unique. Rows whose string matches
# nothing (anonymous scans, deleted users, ambiguous names) keep a null FK; that
# irrecoverable loss is exactly the shape the old string-only column caused and
# is expected/acceptable. The legacy ``*_by`` strings are left untouched.
#
# Modelled on facilities/0002_migrate_asset_requirements and
# inventory/0085_migrate_hazmat_to_safety_profile.

from django.conf import settings
from django.db import migrations


def _match_user(User, name):
    """Return the unique User matching ``name`` by username then handle, else None.

    ``username`` is tried first (it is the value the legacy write paths stored);
    ``handle`` is the fallback. A match counts only when it is unambiguous
    (exactly one row) — both columns are unique in the schema, so this is really
    a belt-and-suspenders guard, but it keeps the "unique match only" contract
    explicit and robust to future data.
    """
    if not name:
        return None
    for field in ("username", "handle"):
        matches = list(User.objects.filter(**{field: name})[:2])
        if len(matches) == 1:
            return matches[0]
    return None


def backfill_actor_users(apps, schema_editor):
    FixtureRefillRequest = apps.get_model("inventory", "FixtureRefillRequest")
    User = apps.get_model(settings.AUTH_USER_MODEL)

    # Only rows carrying at least one legacy name string can be backfilled.
    queryset = FixtureRefillRequest.objects.exclude(requested_by="", resolved_by="").iterator()
    for req in queryset:
        changed = []
        if req.requested_user_id is None:
            user = _match_user(User, req.requested_by)
            if user is not None:
                req.requested_user = user
                changed.append("requested_user")
        if req.resolved_user_id is None:
            user = _match_user(User, req.resolved_by)
            if user is not None:
                req.resolved_user = user
                changed.append("resolved_user")
        if changed:
            req.save(update_fields=changed)


def clear_actor_users(apps, schema_editor):
    """Reverse: drop the recovered links (the legacy ``*_by`` strings remain the
    source of truth, exactly as before this migration)."""
    FixtureRefillRequest = apps.get_model("inventory", "FixtureRefillRequest")
    FixtureRefillRequest.objects.exclude(
        requested_user__isnull=True, resolved_user__isnull=True
    ).update(requested_user=None, resolved_user=None)


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0088_actor_identity_fixture_refill"),
    ]

    operations = [
        migrations.RunPython(backfill_actor_users, clear_actor_users),
    ]
