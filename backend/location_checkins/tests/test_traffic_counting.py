"""
Tests for IoT traffic-count webhook + reporting endpoints (oms-aib).
"""

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from inventory.tests.factories import LocationFactory
from location_checkins.models import LocationCheckIn

PING_TOKEN = "test-ping-token-please-rotate"


@pytest.fixture(autouse=True)
def configure_ping_token(settings):
    settings.LOCATION_PING_TOKEN = PING_TOKEN


@pytest.fixture
def location(db):
    return LocationFactory(is_active=True)


@pytest.fixture
def anon_client():
    return APIClient()


def _post_webhook(client, body, token=PING_TOKEN, header=True):
    url = reverse("location-ping-webhook")
    if header:
        return client.post(url, body, format="json", HTTP_X_LOCATION_PING_TOKEN=token)
    return client.post(f"{url}?token={token}", body, format="json")


# --- Webhook ----------------------------------------------------------------


def test_webhook_with_valid_token_creates_anonymous_checkin(anon_client, location):
    response = _post_webhook(
        anon_client,
        {"location_id": location.pk, "device_id": "esp32-machineroom"},
    )
    assert response.status_code == 204
    checkin = LocationCheckIn.objects.get()
    assert checkin.location_id == location.pk
    assert checkin.user is None
    assert checkin.checkin_type == "anonymous"
    assert checkin.source == "device"
    assert checkin.device_id == "esp32-machineroom"


def test_webhook_invalid_token_returns_401(anon_client, location):
    response = _post_webhook(anon_client, {"location_id": location.pk}, token="nope")
    assert response.status_code == 401
    assert LocationCheckIn.objects.count() == 0


def test_webhook_returns_503_when_token_unconfigured(anon_client, location, settings):
    settings.LOCATION_PING_TOKEN = ""
    response = _post_webhook(anon_client, {"location_id": location.pk}, token="")
    assert response.status_code == 503


def test_webhook_query_token_is_accepted(anon_client, location):
    response = _post_webhook(anon_client, {"location_id": location.pk}, header=False)
    assert response.status_code == 204


def test_webhook_unknown_location_returns_400(anon_client, db):
    response = _post_webhook(anon_client, {"location_id": 999999})
    assert response.status_code == 400
    assert LocationCheckIn.objects.count() == 0


def test_webhook_idempotency_dedupes_within_60s(anon_client, location):
    body = {"location_id": location.pk, "event_id": "evt-123"}
    r1 = _post_webhook(anon_client, body)
    r2 = _post_webhook(anon_client, body)
    assert r1.status_code == 204
    assert r2.status_code == 204
    assert LocationCheckIn.objects.count() == 1


def test_webhook_same_event_id_after_60s_creates_new_checkin(anon_client, location):
    body = {"location_id": location.pk, "event_id": "evt-456"}
    assert _post_webhook(anon_client, body).status_code == 204
    # Backdate the existing row past the dedupe window.
    LocationCheckIn.objects.update(checked_in_at=timezone.now() - timedelta(seconds=120))
    assert _post_webhook(anon_client, body).status_code == 204
    assert LocationCheckIn.objects.count() == 2


def test_webhook_resolves_location_by_access_code(anon_client, db):
    loc = LocationFactory(is_active=True, access_code="ABC234")
    response = _post_webhook(anon_client, {"access_code": "ABC234"})
    assert response.status_code == 204
    assert LocationCheckIn.objects.get().location_id == loc.pk


def test_webhook_uses_supplied_occurred_at(anon_client, location):
    occurred = timezone.now() - timedelta(hours=3)
    response = _post_webhook(
        anon_client,
        {"location_id": location.pk, "occurred_at": occurred.isoformat()},
    )
    assert response.status_code == 204
    saved = LocationCheckIn.objects.get()
    assert abs((saved.checked_in_at - occurred).total_seconds()) < 1


# --- Reports ----------------------------------------------------------------


@pytest.fixture
def auth_client(authenticated_client):
    client, _user = authenticated_client
    return client


def _make_checkins(location, when_list, source="device"):
    rows = [
        LocationCheckIn(
            location=location,
            checkin_type="anonymous",
            source=source,
            checked_in_at=ts,
        )
        for ts in when_list
    ]
    LocationCheckIn.objects.bulk_create(rows)


def test_traffic_by_hour_buckets_correctly(auth_client, location):
    base = timezone.now().replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
    _make_checkins(
        location,
        [
            base,
            base + timedelta(minutes=15),
            base + timedelta(minutes=45),
            base + timedelta(hours=1),
        ],
    )
    url = reverse("location-traffic-report")
    response = auth_client.get(
        url,
        {
            "location": location.pk,
            "start": (base - timedelta(hours=1)).isoformat(),
            "end": (base + timedelta(hours=2)).isoformat(),
            "bucket": "hour",
        },
    )
    assert response.status_code == 200
    results = response.json()["results"]
    counts = [r["count"] for r in results]
    assert counts == [3, 1]


def test_traffic_endpoint_rejects_bad_bucket(auth_client, location):
    response = auth_client.get(
        reverse("location-traffic-report"),
        {"location": location.pk, "bucket": "decade"},
    )
    assert response.status_code == 400


def test_traffic_endpoint_requires_authentication(anon_client, location):
    response = anon_client.get(reverse("location-traffic-report"))
    assert response.status_code in (401, 403)


def test_top_locations_orders_correctly_with_tie_breaks(auth_client, db):
    busy = LocationFactory(name="Busy Room", is_active=True)
    quiet = LocationFactory(name="Quiet Room", is_active=True)
    tied_a = LocationFactory(name="Aardvark Room", is_active=True)
    tied_b = LocationFactory(name="Zebra Room", is_active=True)

    now = timezone.now()
    _make_checkins(busy, [now - timedelta(minutes=i) for i in range(10)])
    _make_checkins(quiet, [now - timedelta(minutes=1)])
    _make_checkins(tied_a, [now, now])
    _make_checkins(tied_b, [now, now])

    response = auth_client.get(
        reverse("location-top-report"),
        {
            "start": (now - timedelta(days=1)).isoformat(),
            "end": (now + timedelta(minutes=1)).isoformat(),
            "limit": 10,
        },
    )
    assert response.status_code == 200
    results = response.json()["results"]
    names = [r["location_name"] for r in results]
    counts = [r["count"] for r in results]

    # Highest count first; ties broken alphabetically by location name.
    assert names[0] == "Busy Room"
    assert counts[0] == 10
    # Tied locations come next, alphabetically.
    assert names[1:3] == ["Aardvark Room", "Zebra Room"]
    assert counts[1] == counts[2] == 2
    assert names[3] == "Quiet Room"
    assert counts[3] == 1
