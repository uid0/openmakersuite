"""Initial BMS schema.

BmsConfig holds one BMS account's OAuth state. ThermostatBinding links
a climate.Thermostat to an external device on a BmsConfig, with a unique
constraint on (config, external_device_id) so re-running discovery is
idempotent. Both encrypted_* binary fields default to empty bytes so a
newly-created row before the OAuth dance is still valid (the adapter
detects empty tokens and raises a useful error).
"""

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("climate", "0002_alter_thermostat_needs_review"),
    ]

    operations = [
        migrations.CreateModel(
            name="BmsConfig",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        help_text="Operator-visible label. Pick something that "
                        "tells you which physical building / account this is "
                        "bound to.",
                        max_length=120,
                        unique=True,
                    ),
                ),
                (
                    "adapter_type",
                    models.CharField(
                        choices=[
                            ("resideo", "Resideo / Honeywell Home Pro"),
                            ("mock", "Mock (testing only)"),
                        ],
                        max_length=32,
                    ),
                ),
                (
                    "encrypted_access_token",
                    models.BinaryField(blank=True, default=b""),
                ),
                (
                    "encrypted_refresh_token",
                    models.BinaryField(blank=True, default=b""),
                ),
                (
                    "access_token_expires_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "last_synced_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "last_sync_error",
                    models.TextField(
                        blank=True,
                        help_text="Adapter exception message from the most "
                        "recent sync. Empty on success.",
                    ),
                ),
            ],
            options={
                "verbose_name": "BMS config",
                "verbose_name_plural": "BMS configs",
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="ThermostatBinding",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "external_device_id",
                    models.CharField(
                        help_text="The BMS-side device identifier (Resideo "
                        "deviceID).",
                        max_length=128,
                    ),
                ),
                (
                    "external_location_id",
                    models.CharField(
                        blank=True,
                        help_text="The BMS-side location identifier — Resideo "
                        "requires locationId on every per-device call.",
                        max_length=128,
                    ),
                ),
                ("indoor_temp_f", models.FloatField(blank=True, null=True)),
                ("indoor_humidity_pct", models.FloatField(blank=True, null=True)),
                ("cool_setpoint_f", models.FloatField(blank=True, null=True)),
                ("heat_setpoint_f", models.FloatField(blank=True, null=True)),
                ("hvac_mode", models.CharField(blank=True, max_length=20)),
                ("fan_mode", models.CharField(blank=True, max_length=20)),
                (
                    "state_raw",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text="Full adapter response on the most recent "
                        "sync — handy for debugging field-mapping issues "
                        "against new device models.",
                    ),
                ),
                ("last_synced_at", models.DateTimeField(blank=True, null=True)),
                ("last_sync_error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "config",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="bindings",
                        to="bms.bmsconfig",
                    ),
                ),
                (
                    "thermostat",
                    models.OneToOneField(
                        help_text="The OMS-side thermostat row this BMS "
                        "device backs.",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="bms_binding",
                        to="climate.thermostat",
                    ),
                ),
            ],
            options={
                "ordering": ["thermostat__location__name"],
            },
        ),
        migrations.AddConstraint(
            model_name="thermostatbinding",
            constraint=models.UniqueConstraint(
                fields=("config", "external_device_id"),
                name="bms_binding_unique_config_device",
            ),
        ),
    ]
