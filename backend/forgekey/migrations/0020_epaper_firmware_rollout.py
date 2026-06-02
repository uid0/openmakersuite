"""EPaper firmware OTA: per-display target + rollout campaign.

Adds two new fields on ``EPaperDisplay`` (a reported-by-device firmware
version string + a target FirmwareVersion FK populated by the rollout
beat task) and a new ``EpaperFirmwareRollout`` model that mirrors
``FirmwareRollout`` in shape and operator UX. The new check endpoint
(``GET /api/forgekey/epaper/<display_id>/firmware-check/``) reads both
sides — the panel reports its current version and the server returns
whatever the rollout has staged as the target.
"""

import uuid

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("forgekey", "0019_firmware_signing_key_cert"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="epaperdisplay",
            name="firmware_version",
            field=models.CharField(
                blank=True,
                help_text=(
                    "Firmware version the panel last reported running (via "
                    "the firmware-check call on each wake)."
                ),
                max_length=50,
            ),
        ),
        migrations.AddField(
            model_name="epaperdisplay",
            name="target_firmware_version",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "FirmwareVersion this panel is being rolled to. Populated "
                    "by EpaperFirmwareRollout's beat task in waves; the check "
                    "endpoint returns its metadata when the panel's reported "
                    "version doesn't match."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="epaper_target_displays",
                to="forgekey.firmwareversion",
            ),
        ),
        migrations.CreateModel(
            name="EpaperFirmwareRollout",
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
                        blank=True,
                        help_text="Optional operator label for the campaign.",
                        max_length=200,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("active", "Active"),
                            ("paused", "Paused"),
                            ("completed", "Completed"),
                            ("cancelled", "Cancelled"),
                        ],
                        default="draft",
                        max_length=20,
                    ),
                ),
                (
                    "batch_size_percent",
                    models.PositiveSmallIntegerField(
                        default=25,
                        help_text="Percent of the target fleet to promote per "
                        "wave (1-100).",
                        validators=[
                            django.core.validators.MinValueValidator(1),
                            django.core.validators.MaxValueValidator(100),
                        ],
                    ),
                ),
                (
                    "interval_minutes",
                    models.PositiveIntegerField(
                        default=30,
                        help_text="Minimum minutes between waves.",
                        validators=[django.core.validators.MinValueValidator(1)],
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "last_advanced_at",
                    models.DateTimeField(
                        blank=True,
                        help_text="When the most recent wave promoted panels.",
                        null=True,
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="epaper_firmware_rollouts",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "firmware_version",
                    models.ForeignKey(
                        help_text="Firmware version this campaign is rolling out.",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="epaper_rollouts",
                        to="forgekey.firmwareversion",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
