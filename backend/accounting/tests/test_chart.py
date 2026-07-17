"""Chart-of-accounts seed tests."""

import pytest
from hordak.models import Account

from accounting.chart import CHART_OF_ACCOUNTS, seed_chart_of_accounts

pytestmark = pytest.mark.django_db


def test_seed_is_idempotent_when_chart_already_present():
    # The 0002 data migration already seeded the chart during DB setup.
    result = seed_chart_of_accounts()
    assert result["created"] == 0
    assert result["existing"] == len(CHART_OF_ACCOUNTS)
    assert result["total"] == 10


def test_seed_recreates_missing_accounts_with_correct_types():
    codes = [code for code, _name, _type in CHART_OF_ACCOUNTS]
    Account.objects.filter(code__in=codes).delete()
    assert Account.objects.filter(code__in=codes).count() == 0

    result = seed_chart_of_accounts()

    assert result["created"] == len(CHART_OF_ACCOUNTS)
    assert result["existing"] == 0
    assert Account.objects.filter(code__in=codes).count() == len(CHART_OF_ACCOUNTS)
    # Types + single USD currency applied on (re)creation.
    assert Account.objects.get(code="5900").type == "EX"
    assert Account.objects.get(code="4200").type == "IN"
    assert Account.objects.get(code="1700").currencies == ["USD"]


def test_seed_command_runs():
    from django.core.management import call_command

    call_command("seed_chart_of_accounts")
    assert Account.objects.filter(code="1200").exists()
