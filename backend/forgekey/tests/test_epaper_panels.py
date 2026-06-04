"""Tests for the e-paper panels management endpoints (list + retire/reactivate)."""

from __future__ import annotations

import uuid

import pytest
from rest_framework.test import APIClient

from forgekey.models import EPaperDisplay
from forgekey.tests.factories import FirmwareVersionFactory
from inventory.tests.factories import AssetFactory

pytestmark = pytest.mark.django_db

LIST_URL = "/api/forgekey/epaper/"


def _set_active_url(display) -> str:
    return f"/api/forgekey/epaper/{display.id}/set-active/"


@pytest.fixture
def api_client(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


def test_list_requires_auth():
    assert APIClient().get(LIST_URL).status_code in (401, 403)


def test_list_returns_panels_with_binding_and_battery(api_client):
    asset = AssetFactory(name="Lathe")
    EPaperDisplay.objects.create(asset=asset, battery_percent=8)
    EPaperDisplay.objects.create(battery_percent=90)  # unbound, healthy

    response = api_client.get(LIST_URL)

    assert response.status_code == 200, response.data
    body = response.json()
    assert len(body) == 2

    bound = next(d for d in body if d["asset"] is not None)
    assert bound["asset_name"] == "Lathe"
    assert bound["battery_percent"] == 8
    assert bound["is_low_battery"] is True

    unbound = next(d for d in body if d["asset"] is None)
    assert unbound["asset_name"] is None
    assert unbound["is_low_battery"] is False


def test_retire_then_reactivate(api_client):
    display = EPaperDisplay.objects.create(battery_percent=50, is_active=True)

    retire = api_client.post(_set_active_url(display), {"is_active": False}, format="json")
    assert retire.status_code == 200, retire.data
    assert retire.json()["is_active"] is False
    display.refresh_from_db()
    assert display.is_active is False

    react = api_client.post(_set_active_url(display), {"is_active": True}, format="json")
    assert react.status_code == 200
    display.refresh_from_db()
    assert display.is_active is True


def test_set_active_unknown_display_returns_404(api_client):
    response = api_client.post(
        f"/api/forgekey/epaper/{uuid.uuid4()}/set-active/",
        {"is_active": False},
        format="json",
    )
    assert response.status_code == 404


def test_list_includes_firmware_and_target(api_client):
    target = FirmwareVersionFactory(version="2.0.0")
    reported = EPaperDisplay.objects.create(firmware_version="1.5.0")
    pending = EPaperDisplay.objects.create(firmware_version="1.5.0", target_firmware_version=target)
    unknown = EPaperDisplay.objects.create()

    response = api_client.get(LIST_URL)
    assert response.status_code == 200, response.data
    by_id = {row["id"]: row for row in response.json()}

    assert by_id[str(reported.id)]["firmware_version"] == "1.5.0"
    assert by_id[str(reported.id)]["target_firmware_version_string"] is None

    assert by_id[str(pending.id)]["firmware_version"] == "1.5.0"
    assert by_id[str(pending.id)]["target_firmware_version_string"] == "2.0.0"

    assert by_id[str(unknown.id)]["firmware_version"] == ""
    assert by_id[str(unknown.id)]["target_firmware_version_string"] is None
