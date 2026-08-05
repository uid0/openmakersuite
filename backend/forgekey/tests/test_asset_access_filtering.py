"""Asset-scoped filtering for the ForgeKey access viewsets (operational mode,
authorizations, lockouts, asset-device bindings).

Added for #7b so the asset detail page can fetch just the records for the
asset it's showing via ``?asset=<id>`` (plus ``?is_active=`` for the
authorization/lockout lists). op-rmic extends the same convention to
``/asset-devices/`` for the "Bound devices" section.

``TestInvalidIdFilters`` (op-7487) covers the whole id-filter family across
these viewsets, including the ``?location=`` / ``?user=`` / ``?actor=`` and
indicator/access-log siblings that share the same ``_filter_by_id`` guard.
"""

import pytest
from rest_framework.test import APIClient

from forgekey.audit import record_event
from forgekey.models import AssetDevice, ForgeKeyAuditEvent
from forgekey.tests.factories import (
    AssetAuthorizationFactory,
    AssetDeviceFactory,
    DeviceLockoutFactory,
    ESP32DeviceFactory,
    IndicatorBindingFactory,
    OperationalModeFactory,
    RoomOperationalModeFactory,
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


# Every ``?<param>=<id>`` filter across the ForgeKey access viewsets, as
# (endpoint path, query param). The pks behind them are split between UUIDs
# (asset, device) and integers (location, user, actor), which raise different
# exceptions out of ``filter()`` — the guard has to cover both.
ID_FILTERS = [
    ("asset-devices", "asset"),
    ("asset-devices", "device"),
    ("operational-modes", "asset"),
    ("room-operational-modes", "location"),
    ("indicator-bindings", "asset"),
    ("indicator-bindings", "device"),
    ("indicator-bindings", "location"),
    ("authorizations", "asset"),
    ("authorizations", "user"),
    ("access-log", "asset"),
    ("access-log", "actor"),
    ("access-log", "device"),
    ("lockouts", "asset"),
]


@pytest.fixture
def one_row_per_endpoint():
    """Seed one real row behind every endpoint in ``ID_FILTERS``.

    The rows matter: they are what distinguishes "the bad id narrowed to
    nothing" from "the bad id was silently dropped and you got the whole
    table back".
    """
    AssetDeviceFactory()
    OperationalModeFactory()
    RoomOperationalModeFactory()
    IndicatorBindingFactory()
    AssetAuthorizationFactory(asset=AssetFactory())
    DeviceLockoutFactory()
    record_event(action=ForgeKeyAuditEvent.ACTION_ACCESS_GRANTED, asset=AssetFactory())


class TestInvalidIdFilters:
    """op-7487: a garbage id in a filter param is a caller mistake, not a 500.

    Before the ``_filter_by_id`` guard these reached the field's own coercion
    inside ``filter()``: the integer pks (``location``, ``user``, ``actor``)
    raised ``ValueError`` and surfaced as an unhandled 500, while the UUID pks
    raised Django's ``ValidationError`` and were translated to a 400 by
    ``standardized_exception_handler``. Same mistake, two different answers,
    neither of them the empty page the caller should get.
    """

    @pytest.mark.parametrize("endpoint,param", ID_FILTERS)
    def test_unparseable_id_returns_empty_page(self, client, one_row_per_endpoint, endpoint, param):
        """Parametrized over every call site: a missed one shows up here."""
        resp = client.get(f"/api/forgekey/{endpoint}/?{param}=abc")

        assert resp.status_code == 200, resp.content
        assert _results(resp) == []

    @pytest.mark.parametrize(
        "endpoint,param",
        [("asset-devices", "asset"), ("authorizations", "user")],
        ids=["uuid-pk", "int-pk"],
    )
    def test_digit_leading_garbage_id_returns_empty_page(
        self, client, one_row_per_endpoint, endpoint, param
    ):
        """A truncated/typo'd id that starts numeric is the realistic mistake,
        and it is invalid for a UUID pk and an integer pk alike."""
        resp = client.get(f"/api/forgekey/{endpoint}/?{param}=12x34")

        assert resp.status_code == 200, resp.content
        assert _results(resp) == []

    @pytest.mark.parametrize(
        "endpoint,param",
        [("asset-devices", "asset"), ("authorizations", "user")],
        ids=["uuid-pk", "int-pk"],
    )
    def test_blank_id_is_not_a_filter(self, client, one_row_per_endpoint, endpoint, param):
        """An empty ``?asset=`` is an absent filter, not an invalid one — it
        must keep returning the unfiltered list rather than narrowing to none."""
        resp = client.get(f"/api/forgekey/{endpoint}/?{param}=")

        assert resp.status_code == 200, resp.content
        assert len(_results(resp)) == 1

    def test_valid_id_still_filters(self, client, one_row_per_endpoint):
        """The guard must not swallow working filters: a real id still narrows."""
        asset = AssetFactory()
        AssetDeviceFactory(asset=asset)

        resp = client.get(f"/api/forgekey/asset-devices/?asset={asset.id}")

        assert resp.status_code == 200, resp.content
        results = _results(resp)
        assert len(results) == 1
        assert str(results[0]["asset"]) == str(asset.id)

    def test_bad_id_narrows_a_combined_filter(self, client):
        """A bad id in one param must not be ignored just because another
        param in the same ``get_queryset`` is valid."""
        asset = AssetFactory()
        AssetAuthorizationFactory(asset=asset, is_active=True)

        resp = client.get("/api/forgekey/authorizations/?user=abc&is_active=true")

        assert resp.status_code == 200, resp.content
        assert _results(resp) == []
