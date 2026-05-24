from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("project_storage", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="projectstoragestint",
            name="printed_at",
            field=models.DateTimeField(
                blank=True,
                help_text=(
                    "When the Pi print daemon last successfully printed the "
                    "label for this stint. NULL means the daemon will pick "
                    "it up on its next poll."
                ),
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="projectstoragestint",
            name="print_target",
            field=models.CharField(
                blank=True,
                choices=[
                    ("brother_ql", "Brother QL label printer"),
                    ("epson_tm", "Epson TM receipt printer"),
                ],
                help_text=(
                    "Which printer family the Pi daemon should send this "
                    "stint's label to. Empty defaults to brother_ql."
                ),
                max_length=16,
            ),
        ),
        migrations.AddIndex(
            model_name="projectstoragestint",
            index=models.Index(
                fields=["printed_at"],
                name="project_sto_printed_3a4f5b_idx",
            ),
        ),
    ]
