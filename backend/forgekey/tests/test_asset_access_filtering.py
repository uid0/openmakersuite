"""Asset-scoped filtering for the ForgeKey access viewsets (operational mode,
authorizations, lockouts, asset-device bindings).

Added for #7b so the asset detail page can fetch just the records for the
asset it's showing via ``?asset=<id>`` (plus ``?is_active=`` for the
authorization/lockout lists). op-rmic extends the same convention to
``/asset-devices/`` for the "Bound devices" section.
"""

import pytest
from rest_framework.test import APIClient

from forgekey.models import AssetDevice
from forgekey.tests.factories import (
    AssetAuthorizationFactory,
    AssetDeviceFactory,
    DeviceLockoutFactory,
    ESP32DeviceFactory,
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


class TestAssetDeviceFilter:
    """op-rmic: the "Bound devices" section reads one asset's bindings."""

    def test_filters_to_single_asset(self, client):
        asset = AssetFactory()
        binding = AssetDeviceFactory(asset=asset, role="power_control")
        AssetDeviceFactory(asset=asset, role="metering", is_primary=False)
        AssetDeviceFactory(asset=AssetFactory())  # unrelated asset

        resp = client.get(f"/api/forgekey/asset-devices/?asset={asset.id}")
        assert resp.status_code == 200, resp.data
        results = _results(resp)
        assert len(results) == 2
        assert {str(r["asset"]) for r in results} == {str(asset.id)}
        # The device columns the section renders come back on the row itself.
        row = next(r for r in results if r["id"] == binding.id)
        assert row["device"] == str(binding.device_id)
        assert row["device_name"] == binding.device.name
        assert row["device_mac_address"] == binding.device.mac_address

    def test_filters_by_device(self, client):
        binding = AssetDeviceFactory()
        AssetDeviceFactory()  # unrelated device

        resp = client.get(f"/api/forgekey/asset-devices/?device={binding.device_id}")
        assert resp.status_code == 200
        results = _results(resp)
        assert len(results) == 1
        assert results[0]["device"] == str(binding.device_id)

    def test_no_filter_returns_all(self, client):
        AssetDeviceFactory()
        AssetDeviceFactory()
        resp = client.get("/api/forgekey/asset-devices/")
        assert resp.status_code == 200
        assert len(_results(resp)) == 2

    def test_attach_and_detach_round_trip(self, client):
        """The section's attach → detach flow against the live endpoint."""
        asset = AssetFactory()
        device = ESP32DeviceFactory()

        created = client.post(
            "/api/forgekey/asset-devices/",
            data={
                "asset": str(asset.id),
                "device": str(device.id),
                "role": "power_control",
                "is_primary": True,
            },
            format="json",
        )
        assert created.status_code == 201, created.data
        assert AssetDevice.objects.filter(asset=asset, device=device).exists()

        detached = client.delete(f"/api/forgekey/asset-devices/{created.data['id']}/")
        assert detached.status_code == 204
        assert not AssetDevice.objects.filter(asset=asset, device=device).exists()
