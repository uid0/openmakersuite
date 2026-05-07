# Generated for gh #358 — append-only audit events for SiteSettings
# updates.

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("customization", "0002_sitesettings_authorized_signature_name_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SiteSettingsAuditEvent",
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
                        choices=[("settings_update", "Site settings updated")],
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
                            "Action-specific payload. For settings_update, "
                            "includes a 'changes' dict mapping field name -> "
                            "{before, after}. File fields are summarized by "
                            "filename only — binary content is never stored "
                            "here."
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
                        related_name="site_settings_audit_actions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "site_settings",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="audit_events",
                        to="customization.sitesettings",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(
                        fields=["site_settings", "-created_at"], name="settings_audit_ss_idx"
                    ),
                    models.Index(fields=["actor", "-created_at"], name="settings_audit_actor_idx"),
                    models.Index(
                        fields=["action", "-created_at"], name="settings_audit_action_idx"
                    ),
                ],
            },
        ),
    ]
