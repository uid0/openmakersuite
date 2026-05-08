"""Seed the DeviceType rows added for the ForgeKey expansion (locker /
door / electronic-device controllers).

Mirrors the ``0004_seed_device_types`` pattern: new codes go in their
own migration so the historical seed stays stable and the test in
``test_provisioning_and_photo.py::test_seed_migration_creates_rows_for_all_choices``
finds every TYPE_CHOICES entry on a fresh database.
"""

from django.db import migrations

NEW_DEVICE_TYPES = [
    ("locker_latch", "Locker latch controller"),
    ("door_latch", "Door latch controller"),
    ("otp_keypad", "OTP keypad"),
    ("led_strip", "WS2818 LED strip controller"),
    ("reed_switch", "Door reed switch"),
    ("ir_break", "Inventory IR-break sensor"),
    ("mortise_key", "Mortise key (admin override) sensor"),
]


def seed(apps, schema_editor):
    DeviceType = apps.get_model("forgekey", "DeviceType")
    for code, label in NEW_DEVICE_TYPES:
        DeviceType.objects.update_or_create(
            code=code,
            defaults={"name": label, "is_active": True},
        )


def unseed(apps, schema_editor):
    DeviceType = apps.get_model("forgekey", "DeviceType")
    DeviceType.objects.filter(code__in=[code for code, _ in NEW_DEVICE_TYPES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("forgekey", "0011_alter_devicetype_code"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
