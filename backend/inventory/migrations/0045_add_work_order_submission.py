import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0044_add_work_orders"),
    ]

    operations = [
        migrations.AddField(
            model_name="workorder",
            name="completed_scan",
            field=models.FileField(
                blank=True,
                help_text=(
                    "Completed work order PDF (attached when a paper form is "
                    "scanned/emailed in via the Postmark inbound webhook)."
                ),
                null=True,
                upload_to="work_orders/scans/%Y/%m/",
            ),
        ),
        migrations.CreateModel(
            name="WorkOrderSubmission",
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
                ("received_at", models.DateTimeField(auto_now_add=True)),
                ("from_email", models.EmailField(blank=True, max_length=254)),
                ("subject", models.CharField(blank=True, max_length=500)),
                (
                    "attachment",
                    models.FileField(
                        help_text="The raw PDF attachment as received from the email",
                        upload_to="work_orders/submissions/%Y/%m/",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("received", "Received"),
                            ("applied", "Applied"),
                            ("failed", "Failed"),
                        ],
                        default="received",
                        max_length=20,
                    ),
                ),
                ("parse_error", models.TextField(blank=True)),
                (
                    "parsed_fields",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text="Snapshot of checkbox values extracted from the PDF",
                    ),
                ),
                (
                    "postmark_message_id",
                    models.CharField(
                        blank=True,
                        db_index=True,
                        help_text="Postmark MessageID header; used for idempotency",
                        max_length=200,
                    ),
                ),
                (
                    "work_order",
                    models.ForeignKey(
                        blank=True,
                        help_text="Resolved work order (null until the PDF has been parsed)",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="submissions",
                        to="inventory.workorder",
                    ),
                ),
            ],
            options={
                "ordering": ["-received_at"],
                "indexes": [
                    models.Index(
                        fields=["status", "-received_at"],
                        name="wos_status_received_idx",
                    ),
                ],
            },
        ),
    ]
