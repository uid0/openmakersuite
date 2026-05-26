"""Tests for PMSchedule status calculation + service-log derived fields."""

from datetime import timedelta

from django.utils import timezone

import pytest

from inventory.tests.factories import AssetFactory
from preventive_maintenance.models import PMSchedule, PMServiceLog

pytestmark = pytest.mark.django_db


def _schedule(interval_days=90, **kwargs):
    return PMSchedule.objects.create(
        asset=kwargs.pop("asset", None) or AssetFactory(),
        task_name=kwargs.pop("task_name", "Replace filter"),
        interval_days=interval_days,
        **kwargs,
    )


class TestPMScheduleStatus:
    def test_never_serviced_returns_never(self):
        s = _schedule()
        assert s.status() == PMSchedule.STATUS_NEVER
        assert s.days_since_last() is None
        assert s.days_until_due() is None

    def test_fresh_log_is_ok(self):
        s = _schedule(interval_days=90)
        PMServiceLog.objects.create(schedule=s, performed_at=timezone.now())
        assert s.status() == PMSchedule.STATUS_OK
        assert s.days_since_last() == 0
        assert s.days_until_due() == 90

    def test_below_warning_threshold_is_ok(self):
        s = _schedule(interval_days=100)
        PMServiceLog.objects.create(schedule=s, performed_at=timezone.now() - timedelta(days=70))
        # 70 days into a 100-day interval = 70% — still ok (warning kicks at 80%)
        assert s.status() == PMSchedule.STATUS_OK

    def test_above_warning_threshold_is_warning(self):
        s = _schedule(interval_days=100)
        PMServiceLog.objects.create(schedule=s, performed_at=timezone.now() - timedelta(days=85))
        # 85% into the interval — warning band (>=80% and <100%)
        assert s.status() == PMSchedule.STATUS_WARNING

    def test_past_due_is_overdue(self):
        s = _schedule(interval_days=30)
        PMServiceLog.objects.create(schedule=s, performed_at=timezone.now() - timedelta(days=45))
        assert s.status() == PMSchedule.STATUS_OVERDUE
        assert s.days_until_due() == -15

    def test_latest_log_wins_when_multiple_present(self):
        s = _schedule(interval_days=30)
        # Old log that would be overdue
        PMServiceLog.objects.create(schedule=s, performed_at=timezone.now() - timedelta(days=200))
        # Recent log that should reset status to ok
        PMServiceLog.objects.create(schedule=s, performed_at=timezone.now() - timedelta(days=2))
        assert s.status() == PMSchedule.STATUS_OK
        assert s.days_since_last() == 2


class TestPMScheduleConstraints:
    def test_unique_asset_task_constraint(self):
        asset = AssetFactory()
        _schedule(asset=asset, task_name="Replace filter")
        # Same asset + same task is rejected by the unique constraint.
        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            _schedule(asset=asset, task_name="Replace filter")

    def test_same_task_different_asset_allowed(self):
        _schedule(task_name="Replace filter")
        # Different asset, same task — allowed.
        _schedule(task_name="Replace filter")
        assert PMSchedule.objects.count() == 2
