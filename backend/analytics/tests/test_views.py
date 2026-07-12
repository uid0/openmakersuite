"""Tests for ``/api/analytics/pulse/`` and ``/api/analytics/share/``."""

from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.utils import timezone
from django.utils.crypto import get_random_string

import pytest
from rest_framework.test import APIClient

from analytics.services.share_token import mint_share_token
from inventory.models import MaintenanceItem, WorkOrder
from inventory.tests.factories import AssetFactory
from membership.models import SIGAdmin

User = get_user_model()
pytestmark = pytest.mark.django_db


def _user(username, **flags):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=get_random_string(24),
        **flags,
    )


def _client(user=None):
    client = APIClient()
    if user:
        client.force_authenticate(user=user)
    return client


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


class TestPulsePermissions:
    URL = "/api/analytics/pulse/"

    def test_anonymous_denied(self):
        resp = _client().get(self.URL)
        assert resp.status_code in (401, 403)

    def test_volunteer_denied(self):
        resp = _client(_user("volunteer")).get(self.URL)
        assert resp.status_code == 403

    def test_staff_allowed(self):
        resp = _client(_user("admin", is_staff=True)).get(self.URL)
        assert resp.status_code == 200

    def test_sig_admin_allowed(self):
        user = _user("sig_lead")
        SIGAdmin.objects.create(user=user, group=Group.objects.create(name="Wood SIG"))
        resp = _client(user).get(self.URL)
        assert resp.status_code == 200


class TestPulseShape:
    URL = "/api/analytics/pulse/"

    def test_returns_all_sections(self):
        resp = _client(_user("admin", is_staff=True)).get(self.URL)
        assert resp.status_code == 200
        body = resp.json()
        for key in (
            "summary",
            "wo_volume_trend",
            "top_users",
            "utilization",
            "category_spend",
            "maintenance_forecast",
        ):
            assert key in body, f"missing section {key}"

    def test_window_override(self):
        client = _client(_user("admin", is_staff=True))
        resp = client.get(self.URL + "?start=2026-01-01&end=2026-02-01")
        assert resp.status_code == 200
        assert resp.json()["summary"]["period_start"] == "2026-01-01"
        assert resp.json()["summary"]["period_end"] == "2026-02-01"

    def test_invalid_bucket(self):
        client = _client(_user("admin", is_staff=True))
        resp = client.get(self.URL + "?bucket=week")
        assert resp.status_code == 400

    def test_partial_window_rejected(self):
        client = _client(_user("admin", is_staff=True))
        resp = client.get(self.URL + "?start=2026-01-01")
        assert resp.status_code == 400

    def test_inverted_window_rejected(self):
        client = _client(_user("admin", is_staff=True))
        resp = client.get(self.URL + "?start=2026-05-01&end=2026-04-01")
        assert resp.status_code == 400

    def test_invalid_date_format(self):
        client = _client(_user("admin", is_staff=True))
        resp = client.get(self.URL + "?start=garbage&end=2026-04-01")
        assert resp.status_code == 400


class TestPulseCache:
    URL = "/api/analytics/pulse/"

    def test_second_request_returns_cached_payload(self):
        """Mutating the underlying data after the first call should not
        change the second call's response — it must come from cache."""
        client = _client(_user("admin", is_staff=True))

        # Use an explicit window so the cache key is stable across calls.
        last_month_end = date.today().replace(day=1)
        last_month_start = (last_month_end - timedelta(days=1)).replace(day=1)
        url = f"{self.URL}?start={last_month_start}&end={last_month_end}"

        first = client.get(url)
        assert first.status_code == 200
        baseline_count = first.json()["summary"]["internal_completed_count"]

        # Mutate the underlying data.
        asset = AssetFactory()
        item = MaintenanceItem.objects.create(asset=asset, title="X", interval_days=30)
        WorkOrder.objects.create(
            maintenance_item=item,
            status=WorkOrder.Status.COMPLETED,
            completed_at=timezone.make_aware(
                datetime.combine(last_month_start + timedelta(days=5), datetime.min.time()),
                timezone.get_current_timezone(),
            ),
            due_date=last_month_start + timedelta(days=5),
            estimated_external_cost=Decimal("10.00"),
        )

        second = client.get(url)
        assert second.status_code == 200
        # Cache hit: count is unchanged despite the new completed WO.
        assert second.json()["summary"]["internal_completed_count"] == baseline_count

        # Sanity check: cache invalidation by clearing and re-requesting picks up the new row.
        cache.clear()
        third = client.get(url)
        assert third.json()["summary"]["internal_completed_count"] == baseline_count + 1


class TestPulseShareToken:
    URL = "/api/analytics/pulse/"

    def test_anonymous_with_valid_token_allowed(self):
        token = mint_share_token(ttl_days=7)
        resp = _client().get(f"{self.URL}?token={token}")
        assert resp.status_code == 200
        assert "summary" in resp.json()

    def test_anonymous_with_invalid_token_denied(self):
        resp = _client().get(f"{self.URL}?token=garbage")
        assert resp.status_code in (401, 403)

    def test_authenticated_without_token_still_works(self):
        # Token is optional — the role gate continues to work.
        resp = _client(_user("admin", is_staff=True)).get(self.URL)
        assert resp.status_code == 200

    def test_volunteer_with_valid_token_allowed(self):
        # Even a volunteer (who would be denied by role) gets in with token.
        token = mint_share_token(ttl_days=7)
        resp = _client(_user("volunteer")).get(f"{self.URL}?token={token}")
        assert resp.status_code == 200


class TestPulseMonthlyBudget:
    URL = "/api/analytics/pulse/"

    def test_budget_null_when_unset(self, settings):
        if hasattr(settings, "MONTHLY_BUDGET_TOTAL"):
            del settings.MONTHLY_BUDGET_TOTAL
        resp = _client(_user("admin", is_staff=True)).get(self.URL)
        assert resp.json()["monthly_budget"] is None

    def test_budget_returned_when_set(self, settings):
        settings.MONTHLY_BUDGET_TOTAL = "5000.00"
        resp = _client(_user("admin", is_staff=True)).get(self.URL)
        assert resp.json()["monthly_budget"] == "5000.00"

    def test_budget_invalid_value_returns_null(self, settings):
        settings.MONTHLY_BUDGET_TOTAL = "not a number"
        resp = _client(_user("admin", is_staff=True)).get(self.URL)
        assert resp.json()["monthly_budget"] is None


class TestShareEndpoint:
    URL = "/api/analytics/share/"

    def test_anonymous_denied(self):
        resp = _client().post(self.URL, data={}, format="json")
        assert resp.status_code in (401, 403)

    def test_volunteer_denied(self):
        resp = _client(_user("volunteer")).post(self.URL, data={}, format="json")
        assert resp.status_code == 403

    def test_staff_can_mint_default_ttl(self):
        resp = _client(_user("admin", is_staff=True)).post(self.URL, data={}, format="json")
        assert resp.status_code == 201
        body = resp.json()
        assert body["ttl_days"] == 30
        assert body["token"]
        assert body["expires_in_days"] == 30

    def test_staff_can_mint_custom_ttl(self):
        resp = _client(_user("admin", is_staff=True)).post(
            self.URL, data={"ttl_days": 7}, format="json"
        )
        assert resp.status_code == 201
        assert resp.json()["ttl_days"] == 7

    def test_minted_token_unlocks_pulse(self):
        share_resp = _client(_user("admin", is_staff=True)).post(
            self.URL, data={"ttl_days": 14}, format="json"
        )
        token = share_resp.json()["token"]
        # Anonymous client uses the token.
        pulse_resp = _client().get(f"/api/analytics/pulse/?token={token}")
        assert pulse_resp.status_code == 200

    def test_invalid_ttl_rejected(self):
        client = _client(_user("admin", is_staff=True))
        for bad in ("hello", -5, 0, 9999):
            resp = client.post(self.URL, data={"ttl_days": bad}, format="json")
            assert resp.status_code == 400

    def test_sig_admin_can_mint(self):
        user = _user("sig_lead")
        SIGAdmin.objects.create(user=user, group=Group.objects.create(name="Wood SIG"))
        resp = _client(user).post(self.URL, data={}, format="json")
        assert resp.status_code == 201
