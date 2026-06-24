"""Asset-scoped filtering for the ForgeKey access viewsets (operational mode,
authorizations, lockouts).

Added for #7b so the asset detail page can fetch just the records for the
asset it's showing via ``?asset=<id>`` (plus ``?is_active=`` for the
authorization/lockout lists).
"""

import pytest
from rest_framework.test import APIClient

from forgekey.tests.factories import (
    AssetAuthorizationFactory,
    DeviceLockoutFactory,
    OperationalModeFactory,
)
from inventory.tests.factories import AssetFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def client(admin_user):
    api = APIClient()
    api.force_authenticate(user=admin_user)
    return api


def _results(response):
    body = response.json()
    return body["results"] if isinstance(body, dict) and "results" in body else body


class TestOperationalModeAssetFilter:
    def test_filters_to_single_asset(self, client):
        asset = AssetFactory()
        OperationalModeFactory(asset=asset)
        OperationalModeFactory(asset=AssetFactory())  # unrelated asset

        resp = client.get(f"/api/forgekey/operational-modes/?asset={asset.id}")
        assert resp.status_code == 200, resp.data
        results = _results(resp)
        assert len(results) == 1
        assert str(results[0]["asset"]) == str(asset.id)

    def test_no_filter_returns_all(self, client):
        OperationalModeFactory()
        OperationalModeFactory()
        resp = client.get("/api/forgekey/operational-modes/")
        assert resp.status_code == 200
        assert len(_results(resp)) == 2


class TestAuthorizationAssetFilter:
    def test_filters_by_asset_and_is_active(self, client):
        asset = AssetFactory()
        AssetAuthorizationFactory(asset=asset, is_active=True)
        AssetAuthorizationFactory(asset=asset, is_active=False)
        AssetAuthorizationFactory(asset=AssetFactory(), is_active=True)  # unrelated

        resp = client.get(f"/api/forgekey/authorizations/?asset={asset.id}")
        assert resp.status_code == 200
        assert len(_results(resp)) == 2  # both of this asset's, regardless of active

        active = client.get(f"/api/forgekey/authorizations/?asset={asset.id}&is_active=true")
        results = _results(active)
        assert len(results) == 1
        assert results[0]["is_active"] is True


class TestAuthorizationUserFilter:
    def test_filters_by_user(self, client):
        """``?user=<id>`` backs the per-member "assets I'm authorized for" view."""
        target = AssetAuthorizationFactory(asset=AssetFactory(), is_active=True)
        AssetAuthorizationFactory(asset=AssetFactory(), is_active=True)  # different user

        resp = client.get(f"/api/forgekey/authorizations/?user={target.user_id}")
        assert resp.status_code == 200, resp.data
        results = _results(resp)
        assert len(results) == 1
        assert results[0]["user"] == target.user_id

    def test_user_and_active_combine(self, client):
        target_user = AssetAuthorizationFactory(asset=AssetFactory(), is_active=True).user
        AssetAuthorizationFactory(asset=AssetFactory(), user=target_user, is_active=False)

        resp = client.get(f"/api/forgekey/authorizations/?user={target_user.id}&is_active=true")
        results = _results(resp)
        assert len(results) == 1
        assert results[0]["is_active"] is True


class TestLockoutAssetFilter:
    def test_filters_by_asset_and_is_active(self, client):
        asset = AssetFactory()
        DeviceLockoutFactory(asset=asset, is_active=True)
        DeviceLockoutFactory(asset=asset, is_active=False)
        DeviceLockoutFactory(asset=AssetFactory(), is_active=True)  # unrelated

        resp = client.get(f"/api/forgekey/lockouts/?asset={asset.id}")
        assert resp.status_code == 200
        assert len(_results(resp)) == 2

        active = client.get(f"/api/forgekey/lockouts/?asset={asset.id}&is_active=1")
        results = _results(active)
        assert len(results) == 1
        assert results[0]["is_active"] is True
