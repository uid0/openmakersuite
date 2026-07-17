"""API tests for the read-only, staff-only accounting endpoints."""

from decimal import Decimal

import pytest

from accounting.models import SourceType
from accounting.serializers import AccountSerializer
from accounting.services import Line, get_account, post_entry

pytestmark = pytest.mark.django_db

ACCOUNTS_URL = "/api/accounting/accounts/"
TRIAL_BALANCE_URL = "/api/accounting/trial-balance/"


def _results(response):
    data = response.data
    return data["results"] if isinstance(data, dict) and "results" in data else data


def test_accounts_list_rejects_anonymous(api_client):
    assert api_client.get(ACCOUNTS_URL).status_code in (401, 403)


def test_accounts_list_rejects_non_staff(api_client):
    from membership.tests.factories import UserFactory

    api_client.force_authenticate(UserFactory(is_staff=False))
    assert api_client.get(ACCOUNTS_URL).status_code == 403


def test_accounts_list_for_admin(api_client, admin_user):
    api_client.force_authenticate(admin_user)
    response = api_client.get(ACCOUNTS_URL)
    assert response.status_code == 200
    rows = _results(response)
    by_code = {row["code"]: row for row in rows}
    assert "1200" in by_code
    ar = by_code["1200"]
    assert ar["type"] == "asset"
    assert ar["full_code"] == "1200"
    assert ar["balance"] == "0.00"  # serialized as a USD decimal string


def test_account_serializer_balance_fallback_without_annotation():
    # Exercises the get_balance() fallback when the queryset was not annotated
    # with with_balances().
    data = AccountSerializer(get_account("1200")).data
    assert data["balance"] == "0.00"
    assert data["type"] == "asset"


def test_trial_balance_rejects_anonymous(api_client):
    assert api_client.get(TRIAL_BALANCE_URL).status_code in (401, 403)


def test_trial_balance_for_admin(api_client, admin_user):
    post_entry(
        lines=[
            Line(account="1300", debit=Decimal("12.00")),
            Line(account="2000", credit=Decimal("12.00")),
        ],
        source_type=SourceType.PO_RECEIPT,
        source_ref="po:api",
    )
    api_client.force_authenticate(admin_user)
    response = api_client.get(TRIAL_BALANCE_URL)
    assert response.status_code == 200
    assert response.data["balanced"] is True
    assert response.data["total_debit"] == "12.00"
    assert response.data["total_credit"] == "12.00"
    by_code = {r["code"]: r for r in response.data["accounts"]}
    assert by_code["1300"]["debit"] == "12.00"


def test_trial_balance_rejects_bad_as_of(api_client, admin_user):
    api_client.force_authenticate(admin_user)
    response = api_client.get(TRIAL_BALANCE_URL, {"as_of": "not-a-date"})
    assert response.status_code == 400


def test_trial_balance_accepts_as_of(api_client, admin_user):
    api_client.force_authenticate(admin_user)
    response = api_client.get(TRIAL_BALANCE_URL, {"as_of": "2000-01-01"})
    assert response.status_code == 200
    assert response.data["balanced"] is True
