# Data migration to migrate Asset operational_status and lockout data to ForgeKey models
# This runs BEFORE removing the old fields from the Asset model

from django.db import migrations


def migrate_operational_status_to_forgekey(apps, schema_editor):
    """
    Migrate operational_status from Asset to OperationalMode in forgekey app.
    """
    Asset = apps.get_model("inventory", "Asset")
    OperationalMode = apps.get_model("forgekey", "OperationalMode")

    # Map old operational_status values to new mode values
    status_to_mode_map = {
        "available": "available",
        "reserved": "available",  # Reserved becomes available
        "needs_maintenance": "maintenance",
        "disabled": "locked_out",  # Disabled becomes locked_out (but only if not already locked)
    }

    migrated_count = 0
    skipped_count = 0

    for asset in Asset.objects.all():
        # Get the old operational_status value
        old_status = getattr(asset, "operational_status", None)

        # Check if asset is already locked (will be handled by lockout migration)
        is_locked = getattr(asset, "is_locked", False)

        if old_status:
            # Map to new mode
            new_mode = status_to_mode_map.get(old_status, "available")

            # If asset is locked, mode should be locked_out (will be set by lockout migration)
            # But we'll set it here too to ensure consistency
            if is_locked:
                new_mode = "locked_out"

            # Create or update OperationalMode
            OperationalMode.objects.update_or_create(
                asset=asset,
                defaults={
                    "mode": new_mode,
                    "classroom_mode_enabled": False,
                },
            )
            migrated_count += 1
        else:
            # Create default OperationalMode for assets without status
            default_mode = "locked_out" if is_locked else "available"
            OperationalMode.objects.get_or_create(
                asset=asset,
                defaults={
                    "mode": default_mode,
                    "classroom_mode_enabled": False,
                },
            )
            skipped_count += 1

    print(
        f"Migrated operational status for {migrated_count} assets to ForgeKey OperationalMode"
    )
    if skipped_count > 0:
        print(
            f"Created default OperationalMode for {skipped_count} assets without status"
        )


def migrate_lockout_data_to_forgekey(apps, schema_editor):
    """
    Migrate lockout data from Asset to DeviceLockout in forgekey app.
    """
    Asset = apps.get_model("inventory", "Asset")
    DeviceLockout = apps.get_model("forgekey", "DeviceLockout")
    OperationalMode = apps.get_model("forgekey", "OperationalMode")

    # Map old lock_type values to new LockoutLevel values
    # LockoutLevel choices: user, maintainer, group_admin, logistics_team, logistics_lead, coo
    lock_type_to_level_map = {
        "admin": "coo",  # Admin becomes COO level
        "group_admin": "group_admin",
        "group_member": "user",  # Group member becomes user level
        "logistics": "logistics_team",
    }

    migrated_count = 0
    for asset in Asset.objects.all():
        # Check if asset is locked
        is_locked = getattr(asset, "is_locked", False)

        if is_locked:
            locked_by = getattr(asset, "locked_by", None)
            locked_at = getattr(asset, "locked_at", None)
            lock_type = getattr(asset, "lock_type", None)

            # Map lock_type to lockout_level
            lockout_level = lock_type_to_level_map.get(lock_type, "user")

            # Create DeviceLockout record
            # Note: We need to set locked_at after creation since auto_now_add might interfere
            lockout = DeviceLockout(
                asset=asset,
                locked_by=locked_by,
                lockout_level=lockout_level,
                reason=f"Migrated from old lock system (type: {lock_type or 'unknown'})",
                is_active=True,
            )
            # Save without locked_at first, then update if we have a value
            lockout.save()
            if locked_at:
                DeviceLockout.objects.filter(id=lockout.id).update(locked_at=locked_at)

            # Ensure OperationalMode is set to locked_out
            OperationalMode.objects.update_or_create(
                asset=asset,
                defaults={
                    "mode": "locked_out",
                },
            )

            migrated_count += 1

    print(
        f"Migrated lockout data for {migrated_count} assets to ForgeKey DeviceLockout"
    )


def reverse_migrate_operational_status(apps, schema_editor):
    """
    Reverse migration: Copy OperationalMode back to Asset.operational_status
    Note: This assumes the fields still exist (for rollback scenarios).
    """
    Asset = apps.get_model("inventory", "Asset")
    OperationalMode = apps.get_model("forgekey", "OperationalMode")

    # Map new mode values back to old operational_status values
    mode_to_status_map = {
        "available": "available",
        "classroom": "available",  # Classroom becomes available
        "maintenance": "needs_maintenance",
        "locked_out": "disabled",
    }

    for mode in OperationalMode.objects.all():
        asset = mode.asset
        old_status = mode_to_status_map.get(mode.mode, "available")

        # Only update if the field still exists (for rollback)
        if hasattr(asset, "operational_status"):
            asset.operational_status = old_status
            asset.save(update_fields=["operational_status"])


def reverse_migrate_lockout_data(apps, schema_editor):
    """
    Reverse migration: Copy DeviceLockout back to Asset lock fields
    Note: This assumes the fields still exist (for rollback scenarios).
    """
    Asset = apps.get_model("inventory", "Asset")
    DeviceLockout = apps.get_model("forgekey", "DeviceLockout")

    # Map new lockout_level values back to old lock_type values
    level_to_lock_type_map = {
        "coo": "admin",
        "logistics_lead": "logistics",
        "logistics_team": "logistics",
        "group_admin": "group_admin",
        "maintainer": "admin",  # Maintainer becomes admin
        "user": "group_member",
    }

    for lockout in DeviceLockout.objects.filter(is_active=True):
        asset = lockout.asset

        # Only update if the fields still exist (for rollback)
        if hasattr(asset, "is_locked"):
            asset.is_locked = True
            asset.locked_by = lockout.locked_by
            asset.locked_at = lockout.locked_at
            asset.lock_type = level_to_lock_type_map.get(lockout.lockout_level, "admin")
            asset.save(
                update_fields=["is_locked", "locked_by", "locked_at", "lock_type"]
            )


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0029_add_asset_part"),
        ("forgekey", "0001_initial"),  # ForgeKey models must exist
    ]

    operations = [
        migrations.RunPython(
            migrate_operational_status_to_forgekey,
            reverse_migrate_operational_status,
        ),
        migrations.RunPython(
            migrate_lockout_data_to_forgekey,
            reverse_migrate_lockout_data,
        ),
    ]
