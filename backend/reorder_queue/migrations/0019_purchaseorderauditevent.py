# Generated for gh #353 — append-only audit events for safety-critical
# purchase-order actions (create, void, line void, mark-delivered,
# attachment add/remove).

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        # Depend on the existing leaf migration, NOT 0018 directly.
        # `0008_webhook_location_problem_event` is a merge migration (its
        # filename is "0008" but it depends on 0018 via the dep graph) and
        # is the actual leaf node on origin/main. Pointing at 0018 instead
        # would create a divergent leaf and `migrate --check` fails CI.
        ("reorder_queue", "0008_webhook_location_problem_event"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PurchaseOrderAuditEvent",
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
                            ("po_create", "Purchase order created"),
                            ("po_void", "Purchase order voided"),
                            ("po_line_void", "Purchase order line item voided"),
                            ("po_mark_delivered", "Purchase order marked delivered"),
                            ("attachment_add", "Attachment added"),
                            ("attachment_remove", "Attachment removed"),
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
                            "Action-specific payload (delivery date, void "
                            "reason, attachment filename, etc)."
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
                        related_name="purchase_order_audit_actions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "purchase_order",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="audit_events",
                        to="reorder_queue.purchaseorder",
                    ),
                ),
                (
                    "line_item",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="audit_events",
                        to="reorder_queue.purchaseorderitem",
                    ),
                ),
                (
                    "attachment",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="audit_events",
                        to="reorder_queue.purchaseorderattachment",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["purchase_order", "-created_at"], name="po_audit_po_idx"),
                    models.Index(fields=["actor", "-created_at"], name="po_audit_actor_idx"),
                    models.Index(fields=["action", "-created_at"], name="po_audit_action_idx"),
                ],
            },
        ),
    ]
