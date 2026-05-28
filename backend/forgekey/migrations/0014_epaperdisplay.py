"""Add EPaperDisplay for the XIAO 7.5" PM panel.

A panel binds one ESP32 (driving the e-paper hardware) to one Asset
whose preventive-maintenance status it renders. Battery and image
ETag fields are stored so ops can spot stale or low-battery panels
without polling each device individually.
"""

import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("forgekey", "0013_certificateauthority_deviceidentity_and_more"),
        ("inventory", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="EPaperDisplay",
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
                    "battery_percent",
                    models.PositiveSmallIntegerField(
                        blank=True,
                        help_text="Last reported battery percentage (0-100).",
                        null=True,
                    ),
                ),
                (
                    "last_battery_at",
                    models.DateTimeField(
                        blank=True,
                        help_text="When the device last reported its battery percentage.",
                        null=True,
                    ),
                ),
                (
                    "last_image_etag",
                    models.CharField(
                        blank=True,
                        help_text="ETag of the most recently rendered PNG (snapshot fingerprint).",
                        max_length=64,
                    ),
                ),
                (
                    "last_image_at",
                    models.DateTimeField(
                        blank=True,
                        help_text="When the panel last fetched a non-304 image.",
                        null=True,
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                        help_text="Inactive panels stop receiving refresh commands.",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "asset",
                    models.ForeignKey(
                        blank=True,
                        help_text="Asset whose PM status the panel is currently showing.",
                        null=True,
                        on_delete=models.deletion.SET_NULL,
                        related_name="epaper_displays",
                        to="inventory.asset",
                    ),
                ),
                (
                    "device",
                    models.OneToOneField(
                        help_text="ESP32 driving the panel.",
                        on_delete=models.deletion.CASCADE,
                        related_name="epaper_display",
                        to="forgekey.esp32device",
                    ),
                ),
            ],
            options={
                "ordering": ["asset__name", "device__mac_address"],
                "indexes": [
                    models.Index(fields=["is_active"], name="forgekey_ep_is_acti_idx"),
                    models.Index(fields=["asset"], name="forgekey_ep_asset_idx"),
                ],
            },
        ),
    ]
