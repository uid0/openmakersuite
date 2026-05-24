import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

import project_storage.models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ProjectStorageStint",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "stint_id",
                    models.CharField(
                        default=project_storage.models._generate_stint_id,
                        max_length=16,
                        unique=True,
                    ),
                ),
                ("username", models.CharField(db_index=True, max_length=64)),
                ("first_name", models.CharField(blank=True, max_length=64)),
                ("last_name", models.CharField(blank=True, max_length=64)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("project_title", models.CharField(blank=True, max_length=120)),
                (
                    "started_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                ("expires_at", models.DateTimeField()),
                ("removed_at", models.DateTimeField(blank=True, null=True)),
                ("notice_sent_at", models.DateTimeField(blank=True, null=True)),
                ("moved_to_purgatory_at", models.DateTimeField(blank=True, null=True)),
                ("storage_location_name", models.CharField(blank=True, max_length=120)),
                ("purgatory_location_name", models.CharField(blank=True, max_length=120)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-started_at"],
            },
        ),
        migrations.CreateModel(
            name="ProjectStorageEvent",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "event_type",
                    models.CharField(
                        choices=[
                            ("created", "Created"),
                            ("scanned", "Scanned"),
                            ("notice_sent", "Violation notice sent"),
                            ("moved_to_purgatory", "Moved to purgatory"),
                            ("removed", "Removed"),
                            ("note_added", "Note added"),
                        ],
                        max_length=32,
                    ),
                ),
                (
                    "actor_label",
                    models.CharField(
                        blank=True,
                        help_text=(
                            "Free-text actor label for kiosk / self-service events "
                            "where no Django user is logged in (e.g. 'kiosk: "
                            "member self-issue')."
                        ),
                        max_length=120,
                    ),
                ),
                ("note", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "stint",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="events",
                        to="project_storage.projectstoragestint",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="projectstoragestint",
            index=models.Index(
                fields=["username", "-started_at"],
                name="project_sto_usernam_8b38d4_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="projectstoragestint",
            index=models.Index(
                fields=["expires_at"],
                name="project_sto_expires_4b879a_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="projectstoragestint",
            index=models.Index(
                fields=["removed_at"],
                name="project_sto_removed_912a13_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="projectstorageevent",
            index=models.Index(
                fields=["stint", "-created_at"],
                name="project_sto_stint_i_421529_idx",
            ),
        ),
    ]
