"""Make ``ESP32Device.device_type`` nullable (op-3he).

The enroll view used to silently skip creating an ESP32Device when the
device's ``sensor_kind`` matched no DeviceType. With the gate removed, the
device must still get a row even when its type is unknown — so the FK has to
allow NULL. ``on_delete`` stays PROTECT (you still cannot delete a DeviceType
that has devices); only ``null``/``blank`` change.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("forgekey", "0025_rename_ac_relay_to_power_relay"),
    ]

    operations = [
        migrations.AlterField(
            model_name="esp32device",
            name="device_type",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Type of device. Nullable: a device whose enroll-time sensor_kind "
                    "has no matching DeviceType is still created (with no type) so it "
                    "appears in the device list rather than being silently dropped "
                    "(op-3he)."
                ),
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="devices",
                to="forgekey.devicetype",
            ),
        ),
    ]
