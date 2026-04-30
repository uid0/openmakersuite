"""Tests for Celery beat schedule registration (AC-32).

Beat-scheduled tasks are easy to break silently: a typo in the task name,
a missing import, or an accidental schedule change will only surface the
next time the scheduler ticks — which can be days or quarters later. These
tests pin the registered name, target task, and cadence so that drift
fails CI instead of going to production.
"""

from django.conf import settings

import pytest
from celery import current_app

EXPECTED_BEAT_SCHEDULE = {
    # name in CELERY_BEAT_SCHEDULE -> (task path, schedule seconds)
    "send-quarterly-donor-updates": (
        "donations.tasks.send_quarterly_donor_updates",
        7776000.0,  # 90 days
    ),
    "flag-expiring-vendor-compliance": (
        "vendors.flag_expiring_compliance",
        86400.0,  # 1 day
    ),
}


@pytest.mark.unit
class TestCeleryBeatSchedule:
    """Pin the registered beat schedule entries (AC-32)."""

    def test_all_expected_entries_present(self):
        for name in EXPECTED_BEAT_SCHEDULE:
            assert name in settings.CELERY_BEAT_SCHEDULE, (
                f"Beat entry {name!r} missing from CELERY_BEAT_SCHEDULE — "
                "scheduled task will not fire in production."
            )

    @pytest.mark.parametrize(
        "name, task_path, schedule_seconds",
        [(name, *cfg) for name, cfg in EXPECTED_BEAT_SCHEDULE.items()],
    )
    def test_entry_targets_expected_task_and_cadence(self, name, task_path, schedule_seconds):
        entry = settings.CELERY_BEAT_SCHEDULE[name]
        assert (
            entry["task"] == task_path
        ), f"Beat entry {name!r} points at {entry['task']!r}, expected {task_path!r}."
        assert entry["schedule"] == schedule_seconds, (
            f"Beat entry {name!r} schedule changed to {entry['schedule']!r} "
            f"(expected {schedule_seconds!r})."
        )

    def test_no_undocumented_entries(self):
        """Every scheduled entry must be in EXPECTED_BEAT_SCHEDULE.

        New entries should be added to docs/CELERY_TASKS.md *and* this test in
        the same PR — that's how the doc stays honest.
        """
        configured = set(settings.CELERY_BEAT_SCHEDULE)
        documented = set(EXPECTED_BEAT_SCHEDULE)
        extra = configured - documented
        assert not extra, (
            f"Beat schedule has undocumented entries: {sorted(extra)}. "
            "Add them to docs/CELERY_TASKS.md and EXPECTED_BEAT_SCHEDULE."
        )


@pytest.mark.unit
class TestScheduledTasksAreRegistered:
    """Each scheduled task name must resolve to an actual registered task.

    Catches the failure mode where someone renames a task function but leaves
    CELERY_BEAT_SCHEDULE pointing at the old dotted path — the scheduler
    enqueues the message and the worker silently drops it as ``KeyError``.

    Celery only loads task modules when the worker starts (via
    :py:meth:`celery.app.base.Celery.autodiscover_tasks`), so registering a
    task by import alone isn't enough — beat will attempt to fire the dotted
    path verbatim. We force the same import path here before checking the
    registry.
    """

    @pytest.fixture(autouse=True)
    def _force_autodiscovery(self):
        # Mirror what the worker does on boot: import every app's tasks
        # module so @shared_task decorators register against the global
        # registry. Without this, only modules imported by other test
        # fixtures show up and the parametrized check is order-dependent.
        current_app.loader.import_default_modules()

    @pytest.mark.parametrize(
        "task_path",
        [cfg[0] for cfg in EXPECTED_BEAT_SCHEDULE.values()],
    )
    def test_scheduled_task_is_registered(self, task_path):
        registered = set(current_app.tasks)
        assert task_path in registered, (
            f"Beat-scheduled task {task_path!r} is not registered. "
            f"Either the path is wrong in CELERY_BEAT_SCHEDULE or the task "
            "module is no longer imported by the worker."
        )
