"""Tests for the Thermostat CRUD API (oms-gzycmj)."""

from __future__ import annotations

from django.contrib.auth import get_user_model

import pytest
from rest_framework.test import APIClient

from climate.models import Thermostat
from inventory.tests.factories import AssetFactory, LocationFactory

User = get_user_model()


@pytest.fixture
def member_client(db):
    user = User.objects.create_user(username="member", email="m@example.com", password="pw")
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def anon_client():
    return APIClient()


@pytest.mark.django_db
def test_anonymous_cannot_list(anon_client):
    resp = anon_client.get("/api/climate/thermostats/")
    assert resp.status_code in (401, 403)


@pytest.mark.django_db
def test_member_can_create_thermostat(member_client):
    room = LocationFactory(name="Thermo API Shop")
    payload = {"location": room.pk, "label": "north"}
    resp = member_client.post("/api/climate/thermostats/", payload, format="json")
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["label"] == "north"
    assert body["location"] == room.pk
    # controls_location auto-defaults to location on save.
    assert body["controls_location"] == room.pk
    assert body["controls_location_name"] == "Thermo API Shop"


@pytest.mark.django_db
def test_thermostat_filter_by_location(member_client):
    a = LocationFactory()
    b = LocationFactory()
    Thermostat.objects.create(location=a, label="A1")
    Thermostat.objects.create(location=b, label="B1")
    resp = member_client.get(f"/api/climate/thermostats/?location={a.pk}")
    assert resp.status_code == 200
    items = resp.json()["results"] if isinstance(resp.json(), dict) else resp.json()
    labels = [i["label"] for i in items]
    assert labels == ["A1"]


@pytest.mark.django_db
def test_thermostat_edit_round_trip(member_client):
    room = LocationFactory()
    asset = AssetFactory(name="RTU-3", location=room)
    t = Thermostat.objects.create(location=room, label="Old label")
    resp = member_client.patch(
        f"/api/climate/thermostats/{t.pk}/",
        {"label": "New label", "controlled_asset": str(asset.pk)},
        format="json",
    )
    assert resp.status_code == 200, resp.content
    t.refresh_from_db()
    assert t.label == "New label"
    assert t.controlled_asset_id == asset.pk
