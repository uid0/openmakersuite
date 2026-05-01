# Generated for oms-k2f: certificate management UI

import uuid

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("forgekey", "0005_occupancyevent"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="FirmwareSigningKey",
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
                    "label",
                    models.CharField(
                        help_text="Human-readable label for this keypair (e.g., 'prod-2026-q2').",
                        max_length=120,
                    ),
                ),
                (
                    "public_key_pem",
                    models.TextField(
                        help_text="PEM-encoded SubjectPublicKeyInfo. Embedded into firmware builds.",
                    ),
                ),
                (
                    "private_key_pem_encrypted",
                    models.BinaryField(
                        help_text="Fernet ciphertext of the PEM-encoded private key.",
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                        help_text="Whether this keypair is the one used to sign new firmware.",
                    ),
                ),
                (
                    "description",
                    models.TextField(blank=True, help_text="Free-form notes for operators."),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "rotated_at",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        help_text="When this key was retired (set automatically on rotation).",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        help_text="User who uploaded / generated this keypair",
                        null=True,
                        on_delete=models.deletion.SET_NULL,
                        related_name="firmware_signing_keys_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "rotated_by",
                    models.ForeignKey(
                        blank=True,
                        help_text="User who retired this key",
                        null=True,
                        on_delete=models.deletion.SET_NULL,
                        related_name="firmware_signing_keys_rotated",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(
                        fields=["is_active", "-created_at"],
                        name="forgekey_fi_is_acti_idx",
                    ),
                ],
            },
        ),
    ]
