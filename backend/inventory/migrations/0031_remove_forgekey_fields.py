# Generated migration to remove ForgeKey fields from Asset model
# These fields have been moved to the forgekey app

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0030_migrate_to_forgekey"),  # Run data migration first
        ("forgekey", "0001_initial"),
    ]

    operations = [
        # Remove operational_status field
        migrations.RemoveField(
            model_name="asset",
            name="operational_status",
        ),
        # Remove lockout fields
        migrations.RemoveField(
            model_name="asset",
            name="is_locked",
        ),
        migrations.RemoveField(
            model_name="asset",
            name="locked_by",
        ),
        migrations.RemoveField(
            model_name="asset",
            name="locked_at",
        ),
        migrations.RemoveField(
            model_name="asset",
            name="lock_type",
        ),
    ]
