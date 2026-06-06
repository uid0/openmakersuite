"""Seed the ``Storage Admin`` Django group.

Members of this group can read project-storage stints (list, retrieve,
by-member lookup) without being platform-wide ``is_staff``. Mutating
actions still require ``IsAdminUser`` — see
``project_storage/permissions.py``.

The group is idempotently created with ``get_or_create`` so re-running
the migration after a manual rename is safe; the reverse migration
removes it (the operator's group memberships go too, which is fine for
a permission-only group).
"""

from __future__ import annotations

from django.db import migrations

STORAGE_ADMIN_GROUP = "Storage Admin"


def create_storage_admin_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.get_or_create(name=STORAGE_ADMIN_GROUP)


def delete_storage_admin_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name=STORAGE_ADMIN_GROUP).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("project_storage", "0002_projectstoragestint_printed_at"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(
            create_storage_admin_group,
            delete_storage_admin_group,
        ),
    ]
