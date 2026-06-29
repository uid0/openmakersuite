"""Backfill ESP32Device rows for already-enrolled devices missing one (op-3he).

Before the enroll-view gate was removed, a device whose normalized
``sensor_kind`` matched no DeviceType (e.g. the relay sending ``power_relay``
against the old ``ac_relay`` code) got a DeviceEnrollment + DeviceCertificate
but NO ESP32Device — so it returned 201 with working mTLS yet never appeared in
the device list. This recreates those orphaned rows the right way: for every
``issued`` enrollment whose MAC has no ESP32Device, create one linked to the
enrollment's identity, with ``device_type`` matched by ``code == sensor_kind``
(NULL when unmapped), copying the MAC / firmware / telemetry the enrollment
carries.

This runs after 0025 (so the relay enrollment's ``power_relay`` sensor_kind now
matches the renamed DeviceType code) and after 0026 (so ``device_type`` may be
NULL for unmapped kinds). It is the data path that recreates the relay row
(MAC 58:8C:81:9E:76:C0 / enrollment 7cd60ac2 / identity 72ebbb0b).
"""

from django.db import migrations


def backfill_missing_esp32devices(apps, schema_editor):
    DeviceEnrollment = apps.get_model("forgekey", "DeviceEnrollment")
    ESP32Device = apps.get_model("forgekey", "ESP32Device")
    DeviceType = apps.get_model("forgekey", "DeviceType")

    handled_macs = set()
    # Most-recent issued enrollment per MAC wins (it carries the live cert).
    issued = (
        DeviceEnrollment.objects.filter(status="issued")
        .exclude(mac_address="")
        .order_by("-requested_at")
    )
    for enr in issued:
        mac = (enr.mac_address or "").strip()
        if not mac or mac in handled_macs:
            continue
        handled_macs.add(mac)
        if ESP32Device.objects.filter(mac_address=mac).exists():
            continue

        device_type = None
        if enr.sensor_kind:
            device_type = DeviceType.objects.filter(code=enr.sensor_kind).first()

        # ``identity`` is a OneToOne; only attach it when still free. It should
        # be free (no ESP32Device exists for this device yet), but guard in case
        # a row was created MAC-first and already claimed this identity.
        identity = enr.device
        if identity is not None and ESP32Device.objects.filter(identity=identity).exists():
            identity = None

        ESP32Device.objects.create(
            mac_address=mac,
            device_type=device_type,
            firmware_version=enr.firmware_version or "",
            boot_count=enr.boot_count,
            free_heap=enr.free_heap,
            ip=enr.ip_address,
            identity=identity,
            last_seen=enr.approved_at or enr.requested_at,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("forgekey", "0026_esp32device_device_type_nullable"),
    ]

    operations = [
        # No clean reverse: we cannot tell which ESP32Device rows this created
        # versus rows added later by the (now-fixed) enroll path.
        migrations.RunPython(backfill_missing_esp32devices, migrations.RunPython.noop),
    ]
