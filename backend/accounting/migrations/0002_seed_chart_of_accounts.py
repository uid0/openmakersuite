"""Seed the Phase-1 chart of accounts (idempotent, reversible).

Delegates to ``accounting.chart.seed_chart_of_accounts`` — the same routine the
``seed_chart_of_accounts`` management command runs — so the migration and the
command can never drift. It uses the real ``hordak.Account`` model (not the
historical one) on purpose: hordak's ``Account.save()`` populates MPTT tree
fields and refreshes the DB-trigger-set ``full_code``, which a frozen
historical model would not do. Depends on hordak's full migration graph so the
account table, its ``full_code`` trigger, and balance functions all exist.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    from accounting.chart import seed_chart_of_accounts

    seed_chart_of_accounts()


def backwards(apps, schema_editor):
    from hordak.models import Account

    from accounting.chart import CHART_OF_ACCOUNTS

    codes = [code for code, _name, _type in CHART_OF_ACCOUNTS]
    # Only remove root accounts we seeded; never cascade into posted ledger data.
    Account.objects.filter(code__in=codes, parent__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0001_initial"),
        ("hordak", "0055_alter_leg_credit_alter_leg_currency_alter_leg_debit"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
