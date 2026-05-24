from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("project_storage", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="projectstoragestint",
            name="printed_at",
            field=models.DateTimeField(blank=True, null=True),
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
                max_length=16,
            ),
        ),
        migrations.AddIndex(
            model_name="projectstoragestint",
            index=models.Index(
                fields=["printed_at"],
                name="project_sto_printed_8860d0_idx",
            ),
        ),
    ]
