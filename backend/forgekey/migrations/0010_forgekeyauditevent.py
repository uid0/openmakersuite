# Generated for gh #352 — append-only audit events for safety-critical
# ForgeKey device actions (authorization grant/revoke, lockout create/unlock,
# firmware request).

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("forgekey", "0009_devicecommand"),
        ("inventory", "0056_asset_is_critical"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ForgeKeyAuditEvent",
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
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "action",
                    models.CharField(
                        choices=[
                            ("authorization_grant", "Authorization granted"),
                            ("authorization_revoke", "Authorization revoked"),
                            ("lockout_create", "Device locked out"),
                            ("lockout_unlock", "Device unlocked"),
                            ("firmware_request", "Firmware update requested"),
                        ],
                        max_length=32,
                    ),
                ),
                ("notes", models.TextField(blank=True)),
                (
                    "metadata",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text=(
                            "Action-specific payload (lockout level, firmware " "version, etc)."
                        ),
                    ),
                ),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        help_text=(
                            "User who performed the action; null for " "system-initiated events."
                        ),
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="forgekey_audit_actions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "asset",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="forgekey_audit_events",
                        to="inventory.asset",
                    ),
                ),
                (
                    "device",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="audit_events",
                        to="forgekey.esp32device",
                    ),
                ),
                (
                    "authorization",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="audit_events",
                        to="forgekey.assetauthorization",
                    ),
                ),
                (
                    "lockout",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="audit_events",
                        to="forgekey.devicelockout",
                    ),
                ),
                (
                    "firmware_update",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="audit_events",
                        to="forgekey.devicefirmwareupdate",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["asset", "-created_at"], name="fk_audit_asset_idx"),
                    models.Index(fields=["device", "-created_at"], name="fk_audit_device_idx"),
                    models.Index(fields=["actor", "-created_at"], name="fk_audit_actor_idx"),
                    models.Index(fields=["action", "-created_at"], name="fk_audit_action_idx"),
                ],
            },
        ),
    ]
