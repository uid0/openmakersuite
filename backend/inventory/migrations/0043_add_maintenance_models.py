# Generated migration for MaintenanceItem, MaintenanceMaterial, MaintenanceLog

import uuid
from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0042_add_resolved_by_to_asset_problem"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MaintenanceItem",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "title",
                    models.CharField(
                        help_text="Brief title for this maintenance task", max_length=200
                    ),
                ),
                (
                    "description",
                    models.TextField(
                        blank=True,
                        help_text="Detailed description of why this maintenance is needed",
                    ),
                ),
                (
                    "instructions",
                    models.TextField(
                        blank=True,
                        help_text="Step-by-step instructions for performing the maintenance",
                    ),
                ),
                (
                    "estimated_time_minutes",
                    models.PositiveIntegerField(
                        blank=True,
                        help_text="Estimated time to complete in minutes",
                        null=True,
                    ),
                ),
                (
                    "estimated_cost",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        help_text="Estimated total cost for materials and labor",
                        max_digits=10,
                    ),
                ),
                (
                    "interval_days",
                    models.PositiveIntegerField(
                        blank=True,
                        help_text="How often this task should be performed (in days). Null means one-time or as-needed.",
                        null=True,
                    ),
                ),
                (
                    "last_completed_at",
                    models.DateTimeField(
                        blank=True,
                        help_text="When this task was last completed",
                        null=True,
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                        help_text="Inactive tasks are hidden from the scan page",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "asset",
                    models.ForeignKey(
                        help_text="The asset this maintenance task belongs to",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="maintenance_items",
                        to="inventory.asset",
                    ),
                ),
            ],
            options={
                "ordering": ["asset", "title"],
            },
        ),
        migrations.AddIndex(
            model_name="maintenanceitem",
            index=models.Index(
                fields=["asset", "is_active"], name="mi_asset_active_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="maintenanceitem",
            index=models.Index(
                fields=["last_completed_at"], name="mi_last_completed_idx"
            ),
        ),
        migrations.CreateModel(
            name="MaintenanceMaterial",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        help_text="Name of the material or supply", max_length=200
                    ),
                ),
                (
                    "quantity",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("1.00"),
                        help_text="Quantity needed",
                        max_digits=10,
                    ),
                ),
                (
                    "unit",
                    models.CharField(
                        blank=True,
                        help_text="Unit of measurement (e.g., pieces, oz, ml, feet)",
                        max_length=50,
                    ),
                ),
                (
                    "estimated_cost_per_unit",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        help_text="Estimated cost per unit",
                        max_digits=10,
                    ),
                ),
                (
                    "notes",
                    models.TextField(blank=True, help_text="Notes about sourcing or usage"),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "maintenance_item",
                    models.ForeignKey(
                        help_text="The maintenance task that requires this material",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="materials",
                        to="inventory.maintenanceitem",
                    ),
                ),
            ],
            options={
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="MaintenanceLog",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("completed_at", models.DateTimeField(auto_now_add=True)),
                (
                    "time_spent_minutes",
                    models.PositiveIntegerField(
                        blank=True,
                        help_text="Actual time spent in minutes",
                        null=True,
                    ),
                ),
                (
                    "cost_incurred",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        help_text="Actual cost incurred for materials and labor",
                        max_digits=10,
                        null=True,
                    ),
                ),
                (
                    "notes",
                    models.TextField(
                        blank=True, help_text="Notes about what was done or observed"
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "completed_by",
                    models.ForeignKey(
                        blank=True,
                        help_text="The user who completed the task",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="maintenance_logs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "maintenance_item",
                    models.ForeignKey(
                        help_text="The maintenance task that was completed",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="logs",
                        to="inventory.maintenanceitem",
                    ),
                ),
            ],
            options={
                "ordering": ["-completed_at"],
            },
        ),
        migrations.AddIndex(
            model_name="maintenancelog",
            index=models.Index(
                fields=["maintenance_item", "completed_at"],
                name="ml_item_completed_idx",
            ),
        ),
    ]
