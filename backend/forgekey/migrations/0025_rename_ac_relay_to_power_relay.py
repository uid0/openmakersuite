"""Rename the relay DeviceType code ``ac_relay`` → ``power_relay`` (op-3he).

The firmware ``sensor_kind`` and the MQTT topic kind both use ``power_relay``
(``FORGEKEY_SENSOR_KIND=power-relay`` → ``normalize_sensor_kind`` → ``power_relay``;
``MQTT_TOPIC_KIND=power_relay``; topics ``forgekey/<mac>/power_relay/...``), but the
model's relay code was the historical ``ac_relay``. The enroll view matches a
DeviceType by ``code == normalized sensor_kind``, so the mismatch meant relays
never matched a type. This migration aligns the code with the firmware/topics.

It (a) updates the ``DeviceType.code`` choices and (b) data-renames the existing
seeded relay row's code in place (the PK / FK identity is preserved, so any rows
pointing at the relay type keep pointing at the same row, now coded
``power_relay``). The human-readable ``name`` ("AC Relay") is intentionally left
unchanged. Historical migrations (0001/0002/0004/0011) keep their ``ac_relay``
references on purpose — they are a stable record of the schema at their time.
"""

from django.db import migrations, models


def rename_ac_relay_to_power_relay(apps, schema_editor):
    DeviceType = apps.get_model("forgekey", "DeviceType")
    # In-place code update: keeps the row's PK so every ESP32Device /
    # FirmwareVersion / FirmwareBuild FK to the relay type is preserved.
    DeviceType.objects.filter(code="ac_relay").update(code="power_relay")


def rename_power_relay_to_ac_relay(apps, schema_editor):
    DeviceType = apps.get_model("forgekey", "DeviceType")
    DeviceType.objects.filter(code="power_relay").update(code="ac_relay")


class Migration(migrations.Migration):

    dependencies = [
        ("forgekey", "0024_assetauthorization_expires_at_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="devicetype",
            name="code",
            field=models.CharField(
                choices=[
                    ("indicator", "Indicator/Status Light"),
                    ("badge_reader", "Badge Reader"),
                    ("epaper_screen", "E-Paper Screen"),
                    ("oled_screen", "OLED Screen"),
                    ("temperature_sensor", "Temperature Sensor"),
                    ("generic_input", "Generic Input"),
                    ("generic_output", "Generic Output"),
                    ("power_relay", "AC Relay"),
                    ("power_measurement", "Power Measurement"),
                    ("people_counter", "People Counter"),
                    ("env_sensor", "Environmental Sensor"),
                    ("door_counter", "Door Counter"),
                    ("locker_latch", "Locker latch controller"),
                    ("door_latch", "Door latch controller"),
                    ("otp_keypad", "OTP keypad"),
                    ("led_strip", "WS2818 LED strip controller"),
                    ("reed_switch", "Door reed switch"),
                    ("ir_break", "Inventory IR-break sensor"),
                    ("mortise_key", "Mortise key (admin override) sensor"),
                ],
                help_text="Device type code",
                max_length=30,
                unique=True,
            ),
        ),
        migrations.RunPython(
            rename_ac_relay_to_power_relay,
            rename_power_relay_to_ac_relay,
        ),
    ]
