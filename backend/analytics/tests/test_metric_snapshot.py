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
    _safe_count,
)
from forgekey.tests.factories import ESP32DeviceFactory
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

    def test_snapshot_emits_every_canonical_gauge(self):
        # The shape contract: every name in METRIC_SNAPSHOT_NAMES must
        # come back with a non-negative integer. Magnitudes depend on
        # other migrations + conftest fixtures, so this test only pins
        # the key set + types — per-subsystem deltas live in the
        # focused tests below.
        snapshot = _collect_metric_snapshot()
        assert set(snapshot.keys()) == set(METRIC_SNAPSHOT_NAMES)
        for name, value in snapshot.items():
            assert isinstance(value, int), f"{name} returned a non-int {value!r}"
            assert value >= 0, f"{name} returned a negative {value}"

    def test_counts_users_staff_and_members(self):
        # Delta against the existing baseline so the test doesn't
        # care how many migration-seeded users already exist.
        before = _collect_metric_snapshot()
        User.objects.create_user(
            username="member-snap-1", email="member-snap-1@test.com", password="snapshot-pw-12345"
        )
        User.objects.create_user(
            username="member-snap-2", email="member-snap-2@test.com", password="snapshot-pw-12345"
        )
        User.objects.create_user(
            username="staff-snap-1",
            email="staff-snap-1@test.com",
            password="snapshot-pw-12345",
            is_staff=True,
        )
        after = _collect_metric_snapshot()
        assert after["oms.metric.user.total"] - before["oms.metric.user.total"] == 3
        assert after["oms.metric.user.staff"] - before["oms.metric.user.staff"] == 1

    def test_counts_active_memberships_only(self):
        before = _collect_metric_snapshot()
        Membership.objects.create(
            membership_type=Membership.MEMBERSHIP_TYPE_MONTHLY,
            status=Membership.STATUS_ACTIVE,
        )
        Membership.objects.create(
            membership_type=Membership.MEMBERSHIP_TYPE_MONTHLY,
            status=Membership.STATUS_INACTIVE,
        )
        after = _collect_metric_snapshot()
        # Inactive membership must not bump the active gauge.
        assert after["oms.metric.membership.active"] - before["oms.metric.membership.active"] == 1

    def test_counts_inventory_locations_items_and_assets(self):
        before = _collect_metric_snapshot()
        location = _make_location("Snapshot L1")
        category = _make_category()
        InventoryItem.objects.create(
            name="Snapshot Bolts",
            category=category,
            location=location,
            reorder_quantity=10,
        )
        InventoryItem.objects.create(
            name="Snapshot Screws",
            category=category,
            location=location,
            reorder_quantity=10,
        )
        Asset.objects.create(name="Snapshot Drill", location=location)
        after = _collect_metric_snapshot()
        # _make_location creates one location, the helper also creates
        # one inside it. Delta accounting:
        assert (
            after["oms.metric.inventory.location.total"]
            - before["oms.metric.inventory.location.total"]
            == 1
        )
        assert (
            after["oms.metric.inventory.item.total"] - before["oms.metric.inventory.item.total"]
            == 2
        )
        assert (
            after["oms.metric.inventory.asset.total"] - before["oms.metric.inventory.asset.total"]
            == 1
        )

    def test_counts_forgekey_device_online_split(self):
        before = _collect_metric_snapshot()
        ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:01", is_online=True)
        ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:02", is_online=False)
        ESP32DeviceFactory(mac_address="AA:BB:CC:00:00:03", is_online=True)
        after = _collect_metric_snapshot()
        assert (
            after["oms.metric.forgekey.device.total"] - before["oms.metric.forgekey.device.total"]
            == 3
        )
        assert (
            after["oms.metric.forgekey.device.online"] - before["oms.metric.forgekey.device.online"]
            == 2
        )

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


class TestSafeCount:
    """`_safe_count` swallows database errors so a single absent table
    doesn't take the whole snapshot down (BACKEND-8). Pin the contract
    so a future refactor doesn't accidentally re-raise."""

    def test_returns_int_on_success(self):
        assert _safe_count("ok", lambda: 5) == 5

    def test_returns_zero_on_programming_error(self):
        from django.db.utils import ProgrammingError

        def raises():
            raise ProgrammingError('relation "missing" does not exist')

        assert _safe_count("missing", raises) == 0

    def test_returns_zero_on_operational_error(self):
        from django.db.utils import OperationalError

        def raises():
            raise OperationalError("connection refused")

        assert _safe_count("conn-down", raises) == 0

    def test_unexpected_exception_propagates(self):
        # _safe_count only swallows DB-shaped errors. A logic bug in
        # the snapshot collector should still surface so it's caught
        # in CI rather than silently zeroing out forever.
        def raises():
            raise RuntimeError("not a db error")

        with pytest.raises(RuntimeError):
            _safe_count("bug", raises)
