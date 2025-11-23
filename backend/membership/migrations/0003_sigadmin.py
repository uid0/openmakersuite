# Generated manually for SIG management system

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("membership", "0002_update_auth_user_custom_fields"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.CreateModel(
            name="SIGAdmin",
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
                    "is_active",
                    models.BooleanField(
                        default=True, help_text="Is this admin role active?"
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "group",
                    models.ForeignKey(
                        help_text="SIG (Group) this user administers",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sig_admins",
                        to="auth.group",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        help_text="User who is an admin of this SIG",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sig_admin_roles",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "SIG Admin",
                "verbose_name_plural": "SIG Admins",
                "ordering": ["group", "user"],
            },
        ),
        migrations.AddIndex(
            model_name="sigadmin",
            index=models.Index(
                fields=["user", "is_active"], name="membership_sigadmin_user_active_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="sigadmin",
            index=models.Index(
                fields=["group", "is_active"],
                name="membership_sigadmin_group_active_idx",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="sigadmin",
            unique_together={("user", "group")},
        ),
    ]
