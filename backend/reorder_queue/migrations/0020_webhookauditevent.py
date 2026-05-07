# Generated for gh #357 — append-only audit events for webhook
# configuration + lifecycle. Depends on 0019_purchaseorderauditevent
# (gh #353) so the two reorder_queue audit tables are introduced in a
# stable order even when their PRs land asynchronously.

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reorder_queue", "0019_purchaseorderauditevent"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="WebhookAuditEvent",
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
                            ("webhook_create", "Webhook created"),
                            ("webhook_update", "Webhook config updated"),
                            ("webhook_delete", "Webhook deleted"),
                            ("webhook_disable", "Webhook disabled"),
                            ("webhook_enable", "Webhook enabled"),
                            ("webhook_secret_rotate", "Webhook secret rotated"),
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
                            "Action-specific payload. For webhook_update, "
                            "includes a 'changes' dict mapping field name -> "
                            "{before, after}. The webhook secret is NEVER "
                            "included — only the fact that it changed is "
                            "recorded."
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
                        related_name="webhook_audit_actions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "webhook",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="audit_events",
                        to="reorder_queue.webhook",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["webhook", "-created_at"], name="webhook_audit_wh_idx"),
                    models.Index(fields=["actor", "-created_at"], name="webhook_audit_actor_idx"),
                    models.Index(fields=["action", "-created_at"], name="webhook_audit_action_idx"),
                ],
            },
        ),
    ]
