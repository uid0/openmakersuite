import uuid
from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0043_add_maintenance_models"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MaintenanceTask",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "order",
                    models.PositiveIntegerField(
                        default=0,
                        help_text="Display order (lower numbers appear first)",
                    ),
                ),
                (
                    "title",
                    models.CharField(
                        max_length=200, help_text="Short title for this step"
                    ),
                ),
                (
                    "description",
                    models.TextField(
                        blank=True,
                        help_text="Detailed instructions for this step",
                    ),
                ),
                (
                    "is_required",
                    models.BooleanField(
                        default=True,
                        help_text="Required steps must be completed before the work order can close",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "maintenance_item",
                    models.ForeignKey(
                        help_text="The maintenance task this step belongs to",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tasks",
                        to="inventory.maintenanceitem",
                    ),
                ),
            ],
            options={
                "ordering": ["order", "title"],
            },
        ),
        migrations.AddIndex(
            model_name="maintenancetask",
            index=models.Index(
                fields=["maintenance_item", "order"], name="mt_item_order_idx"
            ),
        ),
        migrations.CreateModel(
            name="WorkOrder",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("open", "Open"),
                            ("in_progress", "In Progress"),
                            ("blocked", "Blocked"),
                            ("completed", "Completed"),
                        ],
                        default="open",
                        max_length=20,
                        help_text="Current status of this work order",
                    ),
                ),
                (
                    "due_date",
                    models.DateField(
                        blank=True,
                        null=True,
                        help_text="Date by which this work order should be completed",
                    ),
                ),
                (
                    "completed_by_name",
                    models.CharField(
                        blank=True,
                        max_length=200,
                        help_text="Name of person who completed the work (for paper form tracking)",
                    ),
                ),
                (
                    "completed_at",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        help_text="When this work order was marked complete",
                    ),
                ),
                (
                    "notes",
                    models.TextField(blank=True, help_text="Notes about this work order"),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "assigned_to",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="assigned_work_orders",
                        to=settings.AUTH_USER_MODEL,
                        help_text="User assigned to complete this work order",
                    ),
                ),
                (
                    "maintenance_item",
                    models.ForeignKey(
                        help_text="The maintenance task this work order is for",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="work_orders",
                        to="inventory.maintenanceitem",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="workorder",
            index=models.Index(
                fields=["maintenance_item", "status"], name="wo_item_status_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="workorder",
            index=models.Index(
                fields=["due_date", "status"], name="wo_due_status_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="workorder",
            index=models.Index(
                fields=["status", "-created_at"], name="wo_status_created_idx"
            ),
        ),
        migrations.CreateModel(
            name="WorkOrderTaskCompletion",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "task_title",
                    models.CharField(
                        max_length=200,
                        help_text="Denormalized task title (preserved if task is later deleted)",
                    ),
                ),
                (
                    "task_order",
                    models.PositiveIntegerField(
                        default=0,
                        help_text="Display order (denormalized from task at creation time)",
                    ),
                ),
                (
                    "is_required",
                    models.BooleanField(default=True, help_text="Whether this step is required"),
                ),
                ("is_completed", models.BooleanField(default=False)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "completed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="completed_work_order_tasks",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="completions",
                        to="inventory.maintenancetask",
                        help_text="The task step (null if task was deleted after WO creation)",
                    ),
                ),
                (
                    "work_order",
                    models.ForeignKey(
                        help_text="The work order this task completion belongs to",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="task_completions",
                        to="inventory.workorder",
                    ),
                ),
            ],
            options={
                "ordering": ["task_order", "task_title"],
            },
        ),
        migrations.AddIndex(
            model_name="workordertaskcompletion",
            index=models.Index(
                fields=["work_order", "is_completed"], name="wotc_wo_completed_idx"
            ),
        ),
        migrations.CreateModel(
            name="WorkOrderMaterialUsage",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "material_name",
                    models.CharField(
                        max_length=200, help_text="Denormalized material name"
                    ),
                ),
                (
                    "quantity_planned",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("1.00"),
                        max_digits=10,
                        help_text="Planned quantity from the maintenance item spec",
                    ),
                ),
                ("unit", models.CharField(blank=True, max_length=50)),
                (
                    "was_used",
                    models.BooleanField(
                        default=False,
                        help_text="Whether this material was actually used",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "material",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="usage_records",
                        to="inventory.maintenancematerial",
                        help_text="The material specification (null if deleted after WO creation)",
                    ),
                ),
                (
                    "work_order",
                    models.ForeignKey(
                        help_text="The work order this material usage belongs to",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="material_usage",
                        to="inventory.workorder",
                    ),
                ),
            ],
            options={
                "ordering": ["material_name"],
            },
        ),
        migrations.CreateModel(
            name="WorkOrderPhoto",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "image",
                    models.ImageField(
                        upload_to="work_order_photos/%Y/%m/",
                        help_text="Photo of the asset or work performed",
                    ),
                ),
                ("caption", models.CharField(blank=True, max_length=500)),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                (
                    "uploaded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="work_order_photos",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "work_order",
                    models.ForeignKey(
                        help_text="The work order this photo belongs to",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="photos",
                        to="inventory.workorder",
                    ),
                ),
            ],
            options={
                "ordering": ["-uploaded_at"],
            },
        ),
    ]
