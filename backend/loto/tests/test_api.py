"""API tests for the LOTO endpoints (oms-78j AC-2, AC-3, AC-5)."""

from __future__ import annotations

from django.urls import reverse

import pytest

from inventory.models import Asset
from inventory.tests.factories import AssetFactory, LocationFactory
from loto.models import AssetEnergySource, LOTODevice


@pytest.fixture
def location(db):
    return LocationFactory()


@pytest.fixture
def asset(db):
    return AssetFactory()


def test_loto_device_endpoints_require_auth(api_client):
    resp = api_client.get(reverse("loto-device-list"))
    assert resp.status_code == 401


def test_loto_device_create_via_api(authenticated_client, location):
    client, _ = authenticated_client
    resp = client.post(
        reverse("loto-device-list"),
        {
            "device_type": LOTODevice.DEVICE_PADLOCK,
            "label": "PAD-007",
            "location": location.pk,
            "status": LOTODevice.STATUS_AVAILABLE,
        },
        format="json",
    )
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["label"] == "PAD-007"
    assert body["device_type"] == "padlock"
    assert body["device_type_display"] == "Padlock"
    assert body["location_name"] == location.name


def test_loto_device_filter_by_status_and_type(authenticated_client, location):
    client, _ = authenticated_client
    LOTODevice.objects.create(device_type=LOTODevice.DEVICE_PADLOCK, label="A1", location=location)
    LOTODevice.objects.create(
        device_type=LOTODevice.DEVICE_TAG,
        label="T1",
        status=LOTODevice.STATUS_MISSING,
    )
    LOTODevice.objects.create(
        device_type=LOTODevice.DEVICE_PADLOCK,
        label="A2",
        status=LOTODevice.STATUS_IN_USE,
    )

    resp = client.get(reverse("loto-device-list"), {"device_type": "padlock"})
    results = resp.json().get("results", resp.json())
    assert {r["label"] for r in results} == {"A1", "A2"}

    resp = client.get(reverse("loto-device-list"), {"status": "missing"})
    results = resp.json().get("results", resp.json())
    assert [r["label"] for r in results] == ["T1"]


def test_energy_source_create_with_devices(authenticated_client, asset):
    client, _ = authenticated_client
    pad = LOTODevice.objects.create(device_type=LOTODevice.DEVICE_PADLOCK, label="P9")
    resp = client.post(
        reverse("loto-energy-source-list"),
        {
            "asset": str(asset.pk),
            "source_type": "electrical",
            "magnitude": "240V",
            "isolation_point": "Panel A / 12",
            "required_devices": [pad.pk],
        },
        format="json",
    )
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["source_type_display"] == "Electrical"
    assert body["asset_name"] == asset.name
    assert len(body["required_devices_detail"]) == 1
    assert body["required_devices_detail"][0]["label"] == "P9"


def test_energy_source_filter_by_asset(authenticated_client, asset):
    client, _ = authenticated_client
    other = AssetFactory()
    AssetEnergySource.objects.create(asset=asset, source_type="electrical")
    AssetEnergySource.objects.create(asset=asset, source_type="pneumatic")
    AssetEnergySource.objects.create(asset=other, source_type="electrical")

    resp = client.get(reverse("loto-energy-source-list"), {"asset": str(asset.pk)})
    results = resp.json().get("results", resp.json())
    types = sorted(r["source_type"] for r in results)
    assert types == ["electrical", "pneumatic"]


def test_loto_requirements_endpoint_empty_state(authenticated_client, asset):
    """AC-4: 'No LOTO required' when no lockout type and no energy sources."""
    client, _ = authenticated_client
    url = reverse("asset-loto-requirements", args=[asset.pk])
    resp = client.get(url)
    assert resp.status_code == 200
    body = resp.json()
    assert body["asset_id"] == str(asset.pk)
    assert body["is_required"] is False
    assert body["energy_sources"] == []
    assert body["lockout_instructions"] == ""


def test_loto_requirements_endpoint_populated(authenticated_client, asset):
    """AC-5: oms-2da serializer can join LOTO data via this endpoint."""
    client, _ = authenticated_client
    asset.lockout_type = Asset.LockoutType.LOTO
    asset.lockout_instructions = "Lock breaker, tag valve, verify zero energy."
    asset.lockout_responsible = "Maintenance lead"
    asset.save()

    pad = LOTODevice.objects.create(device_type=LOTODevice.DEVICE_PADLOCK, label="P1")
    source = AssetEnergySource.objects.create(
        asset=asset,
        source_type="electrical",
        magnitude="240V",
        isolation_point="Panel A / 12",
    )
    source.required_devices.add(pad)

    url = reverse("asset-loto-requirements", args=[asset.pk])
    body = client.get(url).json()

    assert body["is_required"] is True
    assert body["lockout_type"] == Asset.LockoutType.LOTO
    assert body["lockout_type_display"] == "LOTO (Lockout / Tagout)"
    assert body["lockout_instructions"].startswith("Lock breaker")
    assert body["lockout_responsible"] == "Maintenance lead"
    assert len(body["energy_sources"]) == 1
    es = body["energy_sources"][0]
    assert es["source_type"] == "electrical"
    assert es["magnitude"] == "240V"
    assert es["required_devices_detail"][0]["label"] == "P1"


def test_loto_requirements_is_required_when_only_energy_sources_set(authenticated_client, asset):
    """If an asset has energy sources but no legacy lockout_type, LOTO is still
    required — the structured model is the new source of truth."""
    client, _ = authenticated_client
    AssetEnergySource.objects.create(asset=asset, source_type="hydraulic")

    url = reverse("asset-loto-requirements", args=[asset.pk])
    body = client.get(url).json()
    assert body["is_required"] is True
    assert len(body["energy_sources"]) == 1


def test_loto_requirements_404_for_unknown_asset(authenticated_client):
    client, _ = authenticated_client
    import uuid

    url = reverse("asset-loto-requirements", args=[uuid.uuid4()])
    resp = client.get(url)
    assert resp.status_code == 404
