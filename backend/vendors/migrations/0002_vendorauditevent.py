# Generated for gh #356 — append-only audit events for vendor
# compliance and lifecycle actions.

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("vendors", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="VendorAuditEvent",
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
                            ("vendor_create", "Vendor created"),
                            ("vendor_deactivate", "Vendor deactivated"),
                            ("vendor_reactivate", "Vendor reactivated"),
                            ("compliance_update", "Compliance fields updated"),
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
                            "Action-specific payload. For compliance_update, "
                            "includes a 'changes' dict mapping field name -> "
                            "{before, after}."
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
                        related_name="vendor_audit_actions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "vendor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="audit_events",
                        to="vendors.vendor",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["vendor", "-created_at"], name="vendor_audit_vendor_idx"),
                    models.Index(fields=["actor", "-created_at"], name="vendor_audit_actor_idx"),
                    models.Index(fields=["action", "-created_at"], name="vendor_audit_action_idx"),
                ],
            },
        ),
    ]
