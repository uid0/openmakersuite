"""Backfill devices.Interface from existing MAC fields (oms-06x).

Sources, in priority order:

1. ``forgekey.ESP32Device.mac_address`` — for each ESP32 device that has at
   least one ``AssetDevice`` link, create one Interface row on the
   most-primary Asset (``is_primary=True`` first, then by id) with
   ``name='wlan0'`` and ``interface_type='wifi'``. Devices with no Asset link
   are skipped (they keep their MAC on ``ESP32Device.mac_address`` and a
   future migration can attach them once an Asset is created).

2. ``inventory.Asset.mac_address`` — for each Asset that has a non-blank
   ``mac_address``, create one Interface row with ``name='eth0'`` and
   ``interface_type='ethernet'``. The ``Asset.mac_address`` column stays put
   as a denormalized convenience field (per AC-2).

The migration is idempotent: existing (asset, mac) Interfaces are reused.
Reverse migration is a no-op so that any post-backfill admin edits survive
a rollback.
"""

import re

from django.db import migrations


def _normalize(value):
    if not value:
        return None
    cleaned = re.sub(r"[:-]", "", value).lower()
    if not re.fullmatch(r"[0-9a-f]{12}", cleaned):
        return None
    return cleaned


def _unique_name(Interface, asset, base_name):
    """Return ``base_name`` if free on this asset, else ``base_name`` plus
    a numeric suffix. Avoids tripping the unique-(asset,name) constraint
    when an asset already has, e.g., a manually created ``eth0``.
    """

    candidate = base_name
    suffix = 1
    while Interface.objects.filter(asset=asset, name=candidate).exists():
        candidate = f"{base_name}-{suffix}"
        suffix += 1
    return candidate


def backfill_interfaces(apps, schema_editor):
    ESP32Device = apps.get_model("forgekey", "ESP32Device")
    AssetDevice = apps.get_model("forgekey", "AssetDevice")
    Asset = apps.get_model("inventory", "Asset")
    Interface = apps.get_model("devices", "Interface")

    # 1) ESP32 devices → wifi Interface on their primary asset, if any.
    for device in ESP32Device.objects.all():
        norm = _normalize(device.mac_address)
        if not norm:
            continue
        link = (
            AssetDevice.objects.filter(device=device)
            .order_by("-is_primary", "id")
            .first()
        )
        if link is None:
            continue
        asset = link.asset
        if Interface.objects.filter(asset=asset, mac_address=norm).exists():
            continue
        Interface.objects.create(
            asset=asset,
            name=_unique_name(Interface, asset, "wlan0"),
            interface_type="wifi",
            mac_address=norm,
            enabled=True,
            description=f"Migrated from ESP32Device {device.id}",
        )

    # 2) Asset.mac_address → ethernet Interface on the asset itself.
    for asset in Asset.objects.exclude(mac_address="").exclude(mac_address__isnull=True):
        norm = _normalize(asset.mac_address)
        if not norm:
            continue
        if Interface.objects.filter(asset=asset, mac_address=norm).exists():
            continue
        Interface.objects.create(
            asset=asset,
            name=_unique_name(Interface, asset, "eth0"),
            interface_type="ethernet",
            mac_address=norm,
            enabled=True,
            description="Migrated from Asset.mac_address",
        )


def noop(apps, schema_editor):
    # Intentionally do not delete migrated rows: admins may have edited them.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0001_initial"),
        ("forgekey", "0007_rename_forgekey_fi_is_acti_idx_forgekey_fi_is_acti_625867_idx"),
        ("inventory", "0055_alter_maintenancelog_completed_at"),
    ]

    operations = [
        migrations.RunPython(backfill_interfaces, noop),
    ]
