# Generated for gh #355 — append-only audit events for preventive
# maintenance work orders and location-problem resolutions.

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0056_asset_is_critical"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MaintenanceAuditEvent",
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
                            ("wo_create", "Work order created"),
                            ("wo_complete", "Work order completed"),
                            ("location_problem_resolve", "Location problem resolved"),
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
                            "Action-specific payload (status transition, " "severity, etc)."
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
                        related_name="maintenance_audit_actions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "work_order",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="audit_events",
                        to="inventory.workorder",
                    ),
                ),
                (
                    "location_problem",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="audit_events",
                        to="inventory.locationproblem",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["work_order", "-created_at"], name="maint_audit_wo_idx"),
                    models.Index(
                        fields=["location_problem", "-created_at"], name="maint_audit_lp_idx"
                    ),
                    models.Index(fields=["actor", "-created_at"], name="maint_audit_actor_idx"),
                    models.Index(fields=["action", "-created_at"], name="maint_audit_action_idx"),
                ],
            },
        ),
    ]
