"""
API tests for HardwiredConnectionViewSet (PR 2/5 of disconnect-promotion).

Covers staff-only CRUD, the convenience read fields that surface the
upstream circuit through the disconnect, the ``?asset=`` and
``?disconnect=`` filters, and the new ``hardwired_connections`` field
on the asset detail payload.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse

import pytest
from rest_framework.test import APIClient

from electrical_circuits.models import (
    Disconnect,
    HardwiredConnection,
    PowerBreaker,
    PowerCircuit,
    PowerPanel,
)
from inventory.tests.factories import AssetFactory, LocationFactory

User = get_user_model()


@pytest.fixture
def staff_client(db):
    user = User.objects.create_user(
        username="hw-staff", email="hw-staff@example.com", password="pw", is_staff=True
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def member_client(db):
    user = User.objects.create_user(
        username="hw-member",
        email="hw-member@example.com",
        password="pw",
        is_staff=False,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _make_disconnect(
    *,
    label="Test disconnect",
    disconnect_type=Disconnect.DISCONNECT_TYPE_UNFUSED,
    circuit_label="C1",
):
    loc = LocationFactory()
    panel = PowerPanel.objects.create(location=loc, name=f"P-{loc.pk}")
    breaker = PowerBreaker.objects.create(panel=panel, position="1", amperage=30)
    circuit = PowerCircuit.objects.create(breaker=breaker, label=circuit_label)
    return Disconnect.objects.create(
        circuit=circuit,
        location=loc,
        label=label,
        disconnect_type=disconnect_type,
    )


@pytest.fixture
def disconnect(db):
    return _make_disconnect()


@pytest.fixture
def asset(db):
    return AssetFactory(name="Dust collector", asset_tag="A-HW-1")


# ---------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------


def test_list_rejects_anonymous(api_client):
    assert api_client.get(reverse("hardwired-connection-list")).status_code == 401


def test_list_rejects_non_staff(member_client):
    assert member_client.get(reverse("hardwired-connection-list")).status_code == 403


def test_create_rejects_non_staff(member_client, asset, disconnect):
    resp = member_client.post(
        reverse("hardwired-connection-list"),
        {"asset": str(asset.id), "disconnect": disconnect.pk},
        format="json",
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------
# Read shape
# ---------------------------------------------------------------------


def test_retrieve_surfaces_circuit_and_disconnect_labels(staff_client, asset, disconnect):
    hc = HardwiredConnection.objects.create(asset=asset, disconnect=disconnect)
    resp = staff_client.get(reverse("hardwired-connection-detail", args=[hc.pk]))
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["asset"] == str(asset.id)
    assert body["asset_name"] == asset.name
    assert body["disconnect"] == disconnect.pk
    assert body["disconnect_label"] == disconnect.label
    # circuit is surfaced read-only via disconnect.circuit
    assert body["circuit"] == disconnect.circuit_id
    assert body["circuit_label"] == disconnect.circuit.label


# ---------------------------------------------------------------------
# Write paths
# ---------------------------------------------------------------------


def test_create_round_trips(staff_client, asset, disconnect):
    resp = staff_client.post(
        reverse("hardwired-connection-list"),
        {
            "asset": str(asset.id),
            "disconnect": disconnect.pk,
            "conductor_size": "10 AWG",
            "conductor_length_ft": 25,
        },
        format="json",
    )
    assert resp.status_code == 201, resp.content
    hc = HardwiredConnection.objects.get(asset=asset, disconnect=disconnect)
    assert hc.conductor_size == "10 AWG"
    assert hc.conductor_length_ft == 25


def test_create_with_none_type_disconnect(staff_client, asset):
    """``disconnect_type='none'`` (breaker serves) is a valid feed: every
    hardwired load still resolves through one Disconnect row even when no
    physical switch exists."""
    none_disc = _make_disconnect(
        label="No-switch disconnect",
        disconnect_type=Disconnect.DISCONNECT_TYPE_NONE,
    )
    resp = staff_client.post(
        reverse("hardwired-connection-list"),
        {"asset": str(asset.id), "disconnect": none_disc.pk},
        format="json",
    )
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["disconnect"] == none_disc.pk
    assert body["circuit"] == none_disc.circuit_id


def test_duplicate_asset_disconnect_pair_is_rejected(staff_client, asset, disconnect):
    HardwiredConnection.objects.create(asset=asset, disconnect=disconnect)
    resp = staff_client.post(
        reverse("hardwired-connection-list"),
        {"asset": str(asset.id), "disconnect": disconnect.pk},
        format="json",
    )
    assert resp.status_code == 400


def test_patch_updates_fields(staff_client, asset, disconnect):
    hc = HardwiredConnection.objects.create(asset=asset, disconnect=disconnect)
    resp = staff_client.patch(
        reverse("hardwired-connection-detail", args=[hc.pk]),
        {"conductor_size": "8 AWG"},
        format="json",
    )
    assert resp.status_code == 200, resp.content
    hc.refresh_from_db()
    assert hc.conductor_size == "8 AWG"


def test_delete_works(staff_client, asset, disconnect):
    hc = HardwiredConnection.objects.create(asset=asset, disconnect=disconnect)
    resp = staff_client.delete(reverse("hardwired-connection-detail", args=[hc.pk]))
    assert resp.status_code == 204
    assert not HardwiredConnection.objects.filter(pk=hc.pk).exists()


# ---------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------


def test_filter_by_asset(staff_client, asset, disconnect):
    other_asset = AssetFactory(name="Other", asset_tag="A-HW-2")
    other_disc = _make_disconnect(label="other")
    HardwiredConnection.objects.create(asset=asset, disconnect=disconnect)
    HardwiredConnection.objects.create(asset=other_asset, disconnect=other_disc)
    resp = staff_client.get(reverse("hardwired-connection-list"), {"asset": str(asset.id)})
    assert resp.status_code == 200
    rows = resp.json()["results"]
    assert len(rows) == 1
    assert rows[0]["asset"] == str(asset.id)


def test_filter_by_disconnect(staff_client, asset):
    a = _make_disconnect(label="a-disc")
    b = _make_disconnect(label="b-disc")
    HardwiredConnection.objects.create(asset=asset, disconnect=a)
    other_asset = AssetFactory(asset_tag="A-HW-3")
    HardwiredConnection.objects.create(asset=other_asset, disconnect=b)
    resp = staff_client.get(reverse("hardwired-connection-list"), {"disconnect": a.pk})
    assert resp.status_code == 200
    rows = resp.json()["results"]
    assert len(rows) == 1
    assert rows[0]["disconnect"] == a.pk


# ---------------------------------------------------------------------
# Asset detail surfacing
# ---------------------------------------------------------------------


def test_asset_detail_includes_hardwired_connections(staff_client, asset, disconnect):
    HardwiredConnection.objects.create(asset=asset, disconnect=disconnect, conductor_size="10 AWG")
    resp = staff_client.get(reverse("asset-detail", args=[asset.id]))
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert "hardwired_connections" in body
    assert len(body["hardwired_connections"]) == 1
    payload = body["hardwired_connections"][0]
    assert payload["disconnect"] == disconnect.pk
    assert payload["disconnect_label"] == disconnect.label
    assert payload["conductor_size"] == "10 AWG"


def test_asset_detail_empty_hardwired_connections(staff_client, asset):
    resp = staff_client.get(reverse("asset-detail", args=[asset.id]))
    assert resp.status_code == 200
    assert resp.json()["hardwired_connections"] == []
