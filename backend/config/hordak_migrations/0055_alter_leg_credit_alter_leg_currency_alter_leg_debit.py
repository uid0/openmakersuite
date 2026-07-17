# OMS-owned reconciliation migration for django-hordak. See
# config/hordak_migrations/__init__.py for why it lives here rather than in the
# pip-installed hordak package. hordak 2.0.0's shipped Leg currency column was
# frozen against a different django-money/py-moneyed/babel than this repo pins
# (a 2024 full-currency choices list, EUR default); this brings the migration
# state in line with the installed models' single-currency USD config
# (choices=[("USD", "US Dollar")], default USD). It is Django field-state only —
# choices/default_currency are Python-level, so it emits no DDL (the credit/
# debit numeric(20, 2) columns are unchanged, which also avoids Postgres's
# "cannot alter a column a view depends on" error from hordak_leg_view).
from django.db import migrations

import djmoney.models.fields


class Migration(migrations.Migration):

    dependencies = [
        ("hordak", "0054_check_debit_credit_positive"),
    ]

    operations = [
        migrations.AlterField(
            model_name="leg",
            name="credit",
            field=djmoney.models.fields.MoneyField(
                blank=True,
                currency_field_name="currency",
                decimal_places=2,
                default=None,
                help_text="Amount of this credit, or NULL if not a credit",
                max_digits=20,
                null=True,
                verbose_name="credit amount",
            ),
        ),
        migrations.AlterField(
            model_name="leg",
            name="currency",
            field=djmoney.models.fields.CurrencyField(
                choices=[("USD", "US Dollar")],
                default="USD",
                editable=False,
                max_length=3,
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="leg",
            name="debit",
            field=djmoney.models.fields.MoneyField(
                blank=True,
                currency_field_name="currency",
                decimal_places=2,
                default=None,
                help_text="Amount of this debit, or NULL if not a debit",
                max_digits=20,
                null=True,
                verbose_name="debit amount",
            ),
        ),
    ]
