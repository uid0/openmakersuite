# Generated for gh #354 — append-only audit events for donation,
# tax-receipt, and disposition actions.

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("donations", "0003_donation_tax_receipt_issued_at_taxreceipt"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DonationAuditEvent",
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
                            ("donation_create", "Donation created"),
                            ("tax_receipt_issued", "Tax receipt issued"),
                            ("tax_receipt_regenerate", "Tax receipt regenerated"),
                            ("item_disposed", "Donation item disposed"),
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
                            "Action-specific payload (serial number, " "disposition method, etc)."
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
                        related_name="donation_audit_actions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "donation",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="audit_events",
                        to="donations.donation",
                    ),
                ),
                (
                    "donation_item",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="audit_events",
                        to="donations.donationitem",
                    ),
                ),
                (
                    "tax_receipt",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="audit_events",
                        to="donations.taxreceipt",
                    ),
                ),
                (
                    "disposition",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="audit_events",
                        to="donations.disposition",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["donation", "-created_at"], name="don_audit_donation_idx"),
                    models.Index(fields=["actor", "-created_at"], name="don_audit_actor_idx"),
                    models.Index(fields=["action", "-created_at"], name="don_audit_action_idx"),
                ],
            },
        ),
    ]
