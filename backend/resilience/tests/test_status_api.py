"""``GET /api/resilience/status/`` — the user-facing status board."""

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

import pytest

from resilience.circuit import (
    STATE_CLOSED,
    STATE_HALF_OPEN,
    STATE_OPEN,
    InMemoryStorage,
    RedisStorage,
    reset_storage,
)
from resilience.models import CircuitBreakerEvent
from resilience.services import service_statuses
from resilience.tests.test_circuit import _BrokenRedis

pytestmark = pytest.mark.django_db

STATUS_URL = "/api/resilience/status/"


@pytest.fixture(autouse=True)
def memory_storage(settings):
    """Hermetic breaker state, breakers explicitly re-enabled (settings turn
    them off under pytest)."""
    settings.CIRCUIT_BREAKERS_ENABLED = True
    settings.CIRCUIT_BREAKER_USE_REDIS = False
    storage = InMemoryStorage()
    reset_storage(storage)
    yield storage
    reset_storage(None)


def _services(response) -> dict:
    return {row["key"]: row for row in response.json()["services"]}


def test_url_is_reversible():
    assert reverse("resilience:status") == STATUS_URL


class TestPermissions:
    def test_anonymous_is_rejected(self, api_client):
        response = api_client.get(STATUS_URL)
        assert response.status_code == 401

    def test_any_authenticated_member_may_read(self, authenticated_client):
        client, _user = authenticated_client
        # Deliberately not staff — ordinary members are the audience.
        assert client.get(STATUS_URL).status_code == 200


class TestHealthyPath:
    def test_reports_every_service_healthy(self, authenticated_client):
        client, _user = authenticated_client
        response = client.get(STATUS_URL)

        assert response.status_code == 200
        body = response.json()
        assert body["degraded"] is False
        assert body["checked_at"].endswith("Z")
        assert set(_services(response)) == {
            "device_control",
            "webhooks",
            "whmcs",
            "common_api",
        }
        for row in body["services"]:
            assert row["state"] == STATE_CLOSED
            assert row["healthy"] is True
            assert row["since"] is None
            assert row["last_error"] is None

    def test_row_shape_is_stable(self, authenticated_client):
        """Frontend (PR3) and ScanTTY (PR4) both pin this contract."""
        client, _user = authenticated_client
        row = _services(client.get(STATUS_URL))["device_control"]
        assert set(row) == {
            "key",
            "label",
            "description",
            "state",
            "healthy",
            "since",
            "last_error",
            "degraded_count",
            "total_count",
        }
        assert row["label"] == "Device control"
        assert row["description"] == "Turning equipment on and off remotely"


class TestDegradedPath:
    def test_open_breaker_surfaces_as_a_degraded_service(
        self, authenticated_client, memory_storage
    ):
        memory_storage.trip_open("mqtt")
        client, _user = authenticated_client
        response = client.get(STATUS_URL)

        assert response.status_code == 200
        assert response.json()["degraded"] is True
        row = _services(response)["device_control"]
        assert row["state"] == STATE_OPEN
        assert row["healthy"] is False
        assert row["degraded_count"] == 1
        assert row["total_count"] == 1

    def test_webhook_family_reports_partial_degradation(self, authenticated_client, memory_storage):
        memory_storage.trip_open("webhook:1")
        memory_storage.to_half_open("webhook:2")
        for endpoint_id in range(3, 13):
            memory_storage.close(f"webhook:{endpoint_id}")

        client, _user = authenticated_client
        row = _services(client.get(STATUS_URL))["webhooks"]
        # "2 of 12 endpoints degraded"
        assert row["state"] == STATE_OPEN
        assert row["degraded_count"] == 2
        assert row["total_count"] == 12

    def test_half_open_service_is_not_healthy(self, authenticated_client, memory_storage):
        memory_storage.to_half_open("common_api")
        client, _user = authenticated_client
        row = _services(client.get(STATUS_URL))["common_api"]
        assert row["state"] == STATE_HALF_OPEN
        assert row["healthy"] is False


class TestSinceAndLastError:
    def test_sourced_from_the_latest_matching_event(self, authenticated_client, memory_storage):
        memory_storage.trip_open("mqtt")
        now = timezone.now()

        stale = CircuitBreakerEvent.objects.create(
            name="mqtt", from_state=STATE_CLOSED, to_state=STATE_OPEN, detail="older trip"
        )
        current = CircuitBreakerEvent.objects.create(
            name="mqtt",
            from_state=STATE_HALF_OPEN,
            to_state=STATE_OPEN,
            detail="MQTT broker did not PUBACK within 5s",
        )
        # auto_now_add can't be set on create; rewrite via UPDATE so the
        # ordering under test is explicit rather than insertion-order luck.
        CircuitBreakerEvent.objects.filter(pk=stale.pk).update(created_at=now - timedelta(hours=3))
        CircuitBreakerEvent.objects.filter(pk=current.pk).update(
            created_at=now - timedelta(minutes=8)
        )

        client, _user = authenticated_client
        row = _services(client.get(STATUS_URL))["device_control"]
        assert row["last_error"] == "MQTT broker did not PUBACK within 5s"
        assert row["since"] == (now - timedelta(minutes=8)).isoformat().replace("+00:00", "Z")

    def test_ignores_events_for_a_different_state(self, authenticated_client, memory_storage):
        memory_storage.trip_open("mqtt")
        CircuitBreakerEvent.objects.create(
            name="mqtt", from_state=STATE_OPEN, to_state=STATE_CLOSED, detail="recovered"
        )

        client, _user = authenticated_client
        row = _services(client.get(STATUS_URL))["device_control"]
        assert row["state"] == STATE_OPEN
        assert row["since"] is None
        assert row["last_error"] is None

    def test_ignores_events_for_a_different_breaker(self, authenticated_client, memory_storage):
        memory_storage.trip_open("mqtt")
        CircuitBreakerEvent.objects.create(
            name="whmcs", from_state=STATE_CLOSED, to_state=STATE_OPEN, detail="not mine"
        )

        client, _user = authenticated_client
        row = _services(client.get(STATUS_URL))["device_control"]
        assert row["last_error"] is None

    def test_family_attributes_to_a_degraded_member(self, authenticated_client, memory_storage):
        memory_storage.close("webhook:1")
        memory_storage.trip_open("webhook:2")
        CircuitBreakerEvent.objects.create(
            name="webhook:2",
            from_state=STATE_CLOSED,
            to_state=STATE_OPEN,
            detail="endpoint refused the connection",
        )

        client, _user = authenticated_client
        row = _services(client.get(STATUS_URL))["webhooks"]
        assert row["last_error"] == "endpoint refused the connection"
        assert row["since"] is not None

    def test_blank_detail_serializes_as_null(self, authenticated_client, memory_storage):
        memory_storage.trip_open("whmcs")
        CircuitBreakerEvent.objects.create(
            name="whmcs", from_state=STATE_CLOSED, to_state=STATE_OPEN, detail=""
        )

        client, _user = authenticated_client
        row = _services(client.get(STATUS_URL))["whmcs"]
        assert row["since"] is not None
        assert row["last_error"] is None

    def test_history_costs_one_query_for_all_services(
        self, memory_storage, django_assert_num_queries
    ):
        """Ten degraded breakers across four services, one history query."""
        for endpoint_id in range(1, 9):
            memory_storage.trip_open(f"webhook:{endpoint_id}")
            CircuitBreakerEvent.objects.create(
                name=f"webhook:{endpoint_id}",
                from_state=STATE_CLOSED,
                to_state=STATE_OPEN,
                detail="down",
            )
        memory_storage.trip_open("mqtt")
        memory_storage.trip_open("whmcs")

        with django_assert_num_queries(1):
            assert len(service_statuses(memory_storage)) == 4


class TestDegradesInsteadOfFailing:
    def test_redis_outage_reports_healthy_rather_than_500(self, authenticated_client):
        """A down breaker store must not take the status board down with it."""
        reset_storage(RedisStorage(_BrokenRedis()))

        client, _user = authenticated_client
        response = client.get(STATUS_URL)

        assert response.status_code == 200
        body = response.json()
        assert body["degraded"] is False
        assert all(row["healthy"] for row in body["services"])
        assert _services(response)["webhooks"]["total_count"] == 0

    def test_breakers_disabled_reports_everything_healthy(
        self, authenticated_client, memory_storage, settings
    ):
        memory_storage.trip_open("mqtt")
        memory_storage.trip_open("webhook:1")
        settings.CIRCUIT_BREAKERS_ENABLED = False

        client, _user = authenticated_client
        response = client.get(STATUS_URL)

        assert response.status_code == 200
        assert response.json()["degraded"] is False
        assert all(row["state"] == STATE_CLOSED for row in response.json()["services"])
