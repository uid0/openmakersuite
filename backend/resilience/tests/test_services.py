"""Service-status registry: descriptor validity and family aggregation."""

import pytest

from resilience.circuit import STATE_CLOSED, STATE_HALF_OPEN, STATE_OPEN, InMemoryStorage
from resilience.services import (
    SERVICE_REGISTRY,
    ServiceDescriptor,
    service_statuses,
    status_snapshot,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def breakers_on(settings):
    """Settings disable breakers under pytest; the resilience suite opts back in."""
    settings.CIRCUIT_BREAKERS_ENABLED = True
    settings.CIRCUIT_BREAKER_USE_REDIS = False


@pytest.fixture
def storage():
    return InMemoryStorage()


def _by_key(rows) -> dict:
    return {row["key"]: row for row in rows}


class TestRegistry:
    def test_registers_the_wired_breakers(self):
        registry = {descriptor.key: descriptor for descriptor in SERVICE_REGISTRY}
        assert set(registry) == {"device_control", "webhooks", "whmcs", "common_api", "email"}
        assert registry["device_control"].breaker == "mqtt"
        assert registry["webhooks"].breaker_prefix == "webhook:"
        assert registry["whmcs"].breaker == "whmcs"
        assert registry["common_api"].breaker == "common_api"
        assert registry["email"].breaker == "email"

    def test_keys_and_labels_are_unique(self):
        keys = [descriptor.key for descriptor in SERVICE_REGISTRY]
        assert len(keys) == len(set(keys))
        labels = [descriptor.label for descriptor in SERVICE_REGISTRY]
        assert len(labels) == len(set(labels))

    def test_descriptor_requires_exactly_one_of_breaker_or_prefix(self):
        with pytest.raises(ValueError):
            ServiceDescriptor(key="k", label="L", description="d")
        with pytest.raises(ValueError):
            ServiceDescriptor(key="k", label="L", description="d", breaker="a", breaker_prefix="a:")


class TestSingleBreakerServices:
    def test_untouched_breaker_reports_healthy(self, storage):
        row = _by_key(service_statuses(storage))["device_control"]
        assert row["state"] == STATE_CLOSED
        assert row["healthy"] is True
        assert row["since"] is None
        assert row["last_error"] is None
        assert row["degraded_count"] == 0
        assert row["total_count"] == 1

    def test_open_breaker_reports_degraded(self, storage):
        storage.trip_open("mqtt")
        row = _by_key(service_statuses(storage))["device_control"]
        assert row["state"] == STATE_OPEN
        assert row["healthy"] is False
        assert row["degraded_count"] == 1
        assert row["total_count"] == 1

    def test_half_open_counts_as_degraded(self, storage):
        storage.to_half_open("whmcs")
        row = _by_key(service_statuses(storage))["whmcs"]
        assert row["state"] == STATE_HALF_OPEN
        assert row["healthy"] is False
        assert row["degraded_count"] == 1

    def test_one_degraded_service_does_not_degrade_the_others(self, storage):
        storage.trip_open("common_api")
        rows = _by_key(service_statuses(storage))
        assert rows["common_api"]["healthy"] is False
        assert rows["device_control"]["healthy"] is True
        assert rows["whmcs"]["healthy"] is True


class TestWebhookFamilyAggregation:
    def test_aggregates_member_breakers(self, storage):
        storage.trip_open("webhook:1")
        storage.to_half_open("webhook:2")
        storage.close("webhook:3")

        row = _by_key(service_statuses(storage))["webhooks"]
        # Worst member wins, so the family reads open.
        assert row["state"] == STATE_OPEN
        assert row["healthy"] is False
        assert row["degraded_count"] == 2
        assert row["total_count"] == 3

    def test_family_is_half_open_when_no_member_is_open(self, storage):
        storage.to_half_open("webhook:1")
        storage.close("webhook:2")
        row = _by_key(service_statuses(storage))["webhooks"]
        assert row["state"] == STATE_HALF_OPEN
        assert row["healthy"] is False
        assert row["degraded_count"] == 1
        assert row["total_count"] == 2

    def test_all_members_closed_is_healthy(self, storage):
        storage.close("webhook:1")
        storage.close("webhook:2")
        row = _by_key(service_statuses(storage))["webhooks"]
        assert row["state"] == STATE_CLOSED
        assert row["healthy"] is True
        assert row["degraded_count"] == 0
        assert row["total_count"] == 2

    def test_no_known_members_is_healthy_with_zero_counts(self, storage):
        row = _by_key(service_statuses(storage))["webhooks"]
        assert row["healthy"] is True
        assert row["degraded_count"] == 0
        assert row["total_count"] == 0

    def test_other_breakers_are_not_swept_into_the_family(self, storage):
        storage.trip_open("mqtt")
        storage.trip_open("whmcs")
        row = _by_key(service_statuses(storage))["webhooks"]
        assert row["healthy"] is True
        assert row["total_count"] == 0


class TestSnapshot:
    def test_degraded_flag_reflects_any_unhealthy_service(self, storage):
        assert status_snapshot(storage)["degraded"] is False
        storage.trip_open("webhook:7")
        assert status_snapshot(storage)["degraded"] is True

    def test_snapshot_lists_every_registered_service(self, storage):
        snapshot = status_snapshot(storage)
        assert [row["key"] for row in snapshot["services"]] == [
            descriptor.key for descriptor in SERVICE_REGISTRY
        ]
        assert snapshot["checked_at"] is not None

    def test_payload_never_leaks_breaker_names_or_config(self, storage):
        """Labels and states only — no breaker names, URLs, or hostnames."""
        storage.trip_open("webhook:1")
        row = _by_key(service_statuses(storage))["webhooks"]
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
        assert "webhook:1" not in str(row)


class TestBreakersDisabled:
    def test_reports_every_service_healthy(self, settings, storage):
        storage.trip_open("mqtt")
        storage.trip_open("webhook:1")
        settings.CIRCUIT_BREAKERS_ENABLED = False

        snapshot = status_snapshot(storage)
        assert snapshot["degraded"] is False
        for row in snapshot["services"]:
            assert row["state"] == STATE_CLOSED
            assert row["healthy"] is True
            assert row["degraded_count"] == 0
            assert row["since"] is None
            assert row["last_error"] is None
