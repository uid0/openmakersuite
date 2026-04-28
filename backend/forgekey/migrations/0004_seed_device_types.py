"""Seed DeviceType rows for every ``DeviceType.TYPE_CHOICES`` code so a fresh
deployment can register devices end-to-end without a manual fixture step
(oms-f9z).
"""

from django.db import migrations

# Mirror of ``DeviceType.TYPE_CHOICES`` at the time this migration was written.
# Keep in sync if codes are renamed or removed; new codes are picked up via a
# follow-up data migration so this one stays a stable historical record.
SEED_DEVICE_TYPES = [
    ("indicator", "Indicator/Status Light"),
    ("badge_reader", "Badge Reader"),
    ("epaper_screen", "E-Paper Screen"),
    ("oled_screen", "OLED Screen"),
    ("temperature_sensor", "Temperature Sensor"),
    ("generic_input", "Generic Input"),
    ("generic_output", "Generic Output"),
    ("ac_relay", "AC Relay"),
    ("power_measurement", "Power Measurement"),
    ("people_counter", "People Counter"),
    ("env_sensor", "Environmental Sensor"),
    ("door_counter", "Door Counter"),
]


def seed_device_types(apps, schema_editor):
    DeviceType = apps.get_model("forgekey", "DeviceType")
    for code, label in SEED_DEVICE_TYPES:
        DeviceType.objects.update_or_create(
            code=code,
            defaults={"name": label, "is_active": True},
        )


def unseed_device_types(apps, schema_editor):
    DeviceType = apps.get_model("forgekey", "DeviceType")
    DeviceType.objects.filter(code__in=[code for code, _ in SEED_DEVICE_TYPES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("forgekey", "0003_alter_firmwareversion_signature"),
    ]

    operations = [
        migrations.RunPython(seed_device_types, unseed_device_types),
    ]
