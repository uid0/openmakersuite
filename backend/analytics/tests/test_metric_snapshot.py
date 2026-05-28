"""Tests for `analytics.tasks.emit_metric_snapshot` + helpers.

The snapshot task is the supported replacement for the retired Sentry
Metrics SDK: every 5 min it emits a fixed set of gauge values to
Sentry Logs so uid0 can chart counts (users, devices, items, etc.) in
the Sentry UI. These tests cover the numeric output and the contract
that every name in METRIC_SNAPSHOT_NAMES is actually populated.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.utils import timezone

import pytest

from analytics.tasks import (
    METRIC_SNAPSHOT_NAMES,
    _collect_metric_snapshot,
    _emit_metric_snapshot_to_sentry,
)
from forgekey.models import ESP32Device
from inventory.models import Asset, InventoryItem, Location
from location_checkins.models import LocationCheckIn
from membership.models import Membership

User = get_user_model()
pytestmark = pytest.mark.django_db


def _make_location(name: str = "Snapshot Loc") -> Location:
    return Location.objects.create(name=name)


def _make_category():
    from inventory.models import Category

    return Category.objects.create(name="Snapshot Cat")


class TestCollectMetricSnapshot:
    """Every gauge name must come back with the right count, and the
    set of keys must equal METRIC_SNAPSHOT_NAMES so the canonical list
    stays in sync with what the task actually emits."""

    def test_empty_db_returns_zero_for_every_name(self):
        snapshot = _collect_metric_snapshot()
        assert set(snapshot.keys()) == set(METRIC_SNAPSHOT_NAMES)
        # An empty database should report zeros, not crash. Sentry's
        # log volume from this task is bounded by the count of names,
        # not the magnitude of the values.
        for name, value in snapshot.items():
            assert value == 0, f"{name} returned {value} on an empty DB"

    def test_counts_users_staff_and_members(self):
        User.objects.create_user(
            username="member-1", email="m1@test.com", password="snapshot-pw-12345"
        )
        User.objects.create_user(
            username="member-2", email="m2@test.com", password="snapshot-pw-12345"
        )
        User.objects.create_user(
            username="staff-1",
            email="s1@test.com",
            password="snapshot-pw-12345",
            is_staff=True,
        )
        snapshot = _collect_metric_snapshot()
        assert snapshot["oms.metric.user.total"] == 3
        assert snapshot["oms.metric.user.staff"] == 1

    def test_counts_active_memberships_only(self):
        Membership.objects.create(
            membership_type=Membership.MEMBERSHIP_TYPE_MONTHLY,
            status=Membership.STATUS_ACTIVE,
        )
        Membership.objects.create(
            membership_type=Membership.MEMBERSHIP_TYPE_MONTHLY,
            status=Membership.STATUS_INACTIVE,
        )
        snapshot = _collect_metric_snapshot()
        assert snapshot["oms.metric.membership.active"] == 1

    def test_counts_inventory_locations_items_and_assets(self):
        location = _make_location("L1")
        category = _make_category()
        InventoryItem.objects.create(
            name="Bolts",
            category=category,
            location=location,
        )
        InventoryItem.objects.create(
            name="Screws",
            category=category,
            location=location,
        )
        Asset.objects.create(name="Drill", location=location)
        snapshot = _collect_metric_snapshot()
        assert snapshot["oms.metric.inventory.location.total"] == 1
        assert snapshot["oms.metric.inventory.item.total"] == 2
        assert snapshot["oms.metric.inventory.asset.total"] == 1

    def test_counts_forgekey_device_online_split(self):
        ESP32Device.objects.create(mac_address="AA:BB:CC:00:00:01", is_online=True)
        ESP32Device.objects.create(mac_address="AA:BB:CC:00:00:02", is_online=False)
        ESP32Device.objects.create(mac_address="AA:BB:CC:00:00:03", is_online=True)
        snapshot = _collect_metric_snapshot()
        assert snapshot["oms.metric.forgekey.device.total"] == 3
        assert snapshot["oms.metric.forgekey.device.online"] == 2

    def test_checkin_last_24h_window_excludes_older(self):
        location = _make_location("Checkin Loc")
        now = timezone.now()
        # In window — 1 hour ago.
        LocationCheckIn.objects.create(
            location=location,
            checked_in_at=now - timedelta(hours=1),
        )
        # Out of window — 2 days ago. Must not be counted.
        old = LocationCheckIn.objects.create(
            location=location,
            checked_in_at=now - timedelta(hours=1),
        )
        LocationCheckIn.objects.filter(pk=old.pk).update(checked_in_at=now - timedelta(days=2))
        snapshot = _collect_metric_snapshot()
        assert snapshot["oms.metric.checkin.location.last_24h"] == 1


class TestEmitMetricSnapshotToSentry:
    """`_emit_metric_snapshot_to_sentry` pushes one Sentry log per gauge
    with a numeric `value` attribute. The contract is what powers the
    Sentry-side charts; if the attribute name or message string drifts,
    Discover queries operators built on top will silently break.

    Tests drive the helper directly with a known dict rather than the
    Celery-wrapped `emit_metric_snapshot` so the assertions don't fight
    the bind=True / crons.monitor decorator stack."""

    @patch("analytics.tasks.sentry_sdk")
    def test_emits_one_log_per_gauge(self, mock_sentry):
        mock_logger = MagicMock()
        mock_sentry.logger = mock_logger
        snapshot = {name: idx for idx, name in enumerate(METRIC_SNAPSHOT_NAMES)}

        _emit_metric_snapshot_to_sentry(snapshot)

        assert mock_logger.info.call_count == len(METRIC_SNAPSHOT_NAMES)
        emitted_names = {call.args[0] for call in mock_logger.info.call_args_list}
        assert emitted_names == set(METRIC_SNAPSHOT_NAMES)

    @patch("analytics.tasks.sentry_sdk")
    def test_value_attribute_carries_the_count(self, mock_sentry):
        mock_logger = MagicMock()
        mock_sentry.logger = mock_logger

        _emit_metric_snapshot_to_sentry({"oms.metric.user.total": 7})

        call = mock_logger.info.call_args_list[0]
        assert call.args[0] == "oms.metric.user.total"
        attrs = call.kwargs["attributes"]
        assert attrs["value"] == 7
        assert attrs["metric.name"] == "oms.metric.user.total"
        assert attrs["metric.kind"] == "gauge"
