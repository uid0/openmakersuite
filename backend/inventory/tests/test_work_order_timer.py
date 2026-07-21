"""Work-order + per-step stopwatch (op-m3so).

A work order done electronically should record how long it actually took, so
``MaintenanceItem.estimated_time_minutes`` can be tuned against real numbers.
Two clocks: a work-order one (wall-time-on-job, including setup and cleanup)
and one per step, of which at most one runs at a time.

The tests advance time by rewriting ``timing_since`` in the database rather than
by mocking ``timezone.now`` — that exercises the real accumulate arithmetic and
is exactly the state a clock left running across a lunch break would be in.
"""

from __future__ import annotations

import io
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

import pytest
import yaml
from pypdf import PdfReader
from rest_framework.test import APIClient

from inventory.models import (
    MaintenanceItem,
    MaintenanceLog,
    MaintenanceTask,
    WorkOrder,
    WorkOrderTaskCompletion,
)
from inventory.serializers import WorkOrderSerializer, WorkOrderTaskCompletionSerializer
from inventory.services.work_order_timer import recorded_minutes
from inventory.tests.factories import AssetFactory
from inventory.utils.work_order_pdf import generate_work_order_pdf

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_client():
    user = User.objects.create_user(username="timekeeper", password="x", is_staff=True)
    api = APIClient()
    api.force_authenticate(user=user)
    return api


@pytest.fixture
def work_order():
    """An open WO on a 30-minute PM with two steps."""
    item = MaintenanceItem.objects.create(
        asset=AssetFactory(),
        title="Replace water filter",
        description="Every 330 days",
        interval_days=330,
        estimated_time_minutes=30,
    )
    wo = WorkOrder.objects.create(
        maintenance_item=item,
        due_date=date.today() + timedelta(days=7),
    )
    for i in range(2):
        task = MaintenanceTask.objects.create(
            maintenance_item=item, order=i, title=f"Step {i + 1}", is_required=True
        )
        WorkOrderTaskCompletion.objects.create(
            work_order=wo,
            task=task,
            task_title=task.title,
            task_order=task.order,
            is_required=task.is_required,
        )
    return wo


def _timer(api, wo, action):
    return api.post(f"/api/inventory/work-orders/{wo.id}/timer/", {"action": action}, format="json")


def _task_timer(api, wo, tc, action):
    return api.post(
        f"/api/inventory/work-orders/{wo.id}/tasks/{tc.id}/timer/",
        {"action": action},
        format="json",
    )


def _rewind(obj, seconds):
    """Backdate the running segment so it looks ``seconds`` old."""
    type(obj).objects.filter(pk=obj.pk).update(
        timing_since=timezone.now() - timedelta(seconds=seconds)
    )
    obj.refresh_from_db()


def _validate(api, wo):
    """Slip the AC-3 validation gate so the WO can be marked complete."""
    resp = api.post(
        f"/api/inventory/work-orders/{wo.id}/validate/",
        {
            "electrical_acknowledged": True,
            "loto_acknowledged": True,
            "required_fields_acknowledged": True,
        },
        format="json",
    )
    assert resp.status_code == 201, resp.content


def _complete(api, wo):
    _validate(api, wo)
    resp = api.patch(
        f"/api/inventory/work-orders/{wo.id}/",
        {"status": WorkOrder.Status.COMPLETED},
        format="json",
    )
    assert resp.status_code == 200, resp.content
    wo.refresh_from_db()
    return resp


def _pdf_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


class TestWorkOrderTimer:
    def test_start_then_pause_accumulates_elapsed(self, staff_client, work_order):
        resp = _timer(staff_client, work_order, "start")
        assert resp.status_code == 200, resp.content
        assert resp.json()["is_timing"] is True
        assert resp.json()["changed"] is True

        work_order.refresh_from_db()
        assert work_order.started_at is not None
        _rewind(work_order, 90)

        resp = _timer(staff_client, work_order, "pause")
        assert resp.status_code == 200, resp.content
        work_order.refresh_from_db()
        assert work_order.is_timing is False
        assert work_order.timing_since is None
        assert 89 <= work_order.elapsed_seconds <= 92

    def test_pause_and_resume_sums_both_segments(self, staff_client, work_order):
        _timer(staff_client, work_order, "start")
        _rewind(work_order, 60)
        _timer(staff_client, work_order, "pause")
        _timer(staff_client, work_order, "start")
        _rewind(work_order, 30)
        _timer(staff_client, work_order, "pause")

        work_order.refresh_from_db()
        # The gap between the two segments is NOT counted — that is the whole
        # point of the accumulator over a single started_at/ended_at pair.
        assert 89 <= work_order.elapsed_seconds <= 93

    def test_start_is_idempotent(self, staff_client, work_order):
        _timer(staff_client, work_order, "start")
        work_order.refresh_from_db()
        first_segment = work_order.timing_since

        resp = _timer(staff_client, work_order, "start")
        assert resp.status_code == 200
        assert resp.json()["changed"] is False

        work_order.refresh_from_db()
        # A second start must not restart the segment — that would silently
        # discard the time already on the clock.
        assert work_order.timing_since == first_segment
        assert work_order.elapsed_seconds == 0

    def test_pause_when_stopped_is_idempotent(self, staff_client, work_order):
        resp = _timer(staff_client, work_order, "pause")
        assert resp.status_code == 200
        assert resp.json()["changed"] is False
        work_order.refresh_from_db()
        assert work_order.elapsed_seconds == 0
        assert work_order.is_timing is False

    def test_started_at_is_stamped_once(self, staff_client, work_order):
        _timer(staff_client, work_order, "start")
        work_order.refresh_from_db()
        first_start = work_order.started_at

        _timer(staff_client, work_order, "pause")
        _timer(staff_client, work_order, "start")
        work_order.refresh_from_db()
        # "When work began", not "when this segment began".
        assert work_order.started_at == first_start

    def test_unknown_action_is_rejected(self, staff_client, work_order):
        resp = _timer(staff_client, work_order, "stahp")
        assert resp.status_code == 400
        assert "start" in resp.json()["detail"]
        work_order.refresh_from_db()
        assert work_order.is_timing is False

    def test_anonymous_cannot_run_the_clock(self, work_order):
        resp = _timer(APIClient(), work_order, "start")
        assert resp.status_code in (401, 403)
        work_order.refresh_from_db()
        assert work_order.is_timing is False


class TestLiveElapsed:
    def test_serializer_reports_the_running_segment(self, work_order):
        work_order.start_timer()
        work_order.save()
        _rewind(work_order, 45)

        data = WorkOrderSerializer(work_order).data
        # The stored column still holds 0 — the live value is what clients read.
        assert work_order.elapsed_seconds == 0
        assert 44 <= data["elapsed_seconds"] <= 47
        assert data["is_timing"] is True
        assert data["started_at"] is not None

    def test_serializer_reports_committed_time_when_paused(self, work_order):
        work_order.elapsed_seconds = 120
        work_order.save()
        data = WorkOrderSerializer(work_order).data
        assert data["elapsed_seconds"] == 120
        assert data["is_timing"] is False

    def test_step_serializer_reports_the_running_segment(self, work_order):
        step = work_order.task_completions.first()
        step.start_timer()
        step.save()
        _rewind(step, 30)

        data = WorkOrderTaskCompletionSerializer(step).data
        assert 29 <= data["elapsed_seconds"] <= 32
        assert data["is_timing"] is True

    def test_estimate_travels_with_the_work_order(self, work_order):
        assert WorkOrderSerializer(work_order).data["estimated_time_minutes"] == 30


class TestStepTimer:
    def test_starting_a_step_pauses_the_other_one(self, staff_client, work_order):
        step_a, step_b = list(work_order.task_completions.order_by("task_order"))

        assert _task_timer(staff_client, work_order, step_a, "start").status_code == 200
        _rewind(step_a, 60)
        assert _task_timer(staff_client, work_order, step_b, "start").status_code == 200

        step_a.refresh_from_db()
        step_b.refresh_from_db()
        # A's time is committed, not lost, and only B is running.
        assert step_a.is_timing is False
        assert 59 <= step_a.elapsed_seconds <= 62
        assert step_b.is_timing is True

    def test_starting_a_step_moves_the_wo_off_open(self, staff_client, work_order):
        assert work_order.status == WorkOrder.Status.OPEN
        step = work_order.task_completions.first()
        _task_timer(staff_client, work_order, step, "start")
        work_order.refresh_from_db()
        assert work_order.status == WorkOrder.Status.IN_PROGRESS

    def test_step_clock_is_independent_of_the_wo_clock(self, staff_client, work_order):
        step = work_order.task_completions.first()
        _task_timer(staff_client, work_order, step, "start")
        work_order.refresh_from_db()
        # WO elapsed covers setup/LOTO/cleanup too, so it is started separately.
        assert work_order.is_timing is False

    def test_completing_a_step_stops_its_clock(self, staff_client, work_order):
        step = work_order.task_completions.first()
        _task_timer(staff_client, work_order, step, "start")
        _rewind(step, 75)

        resp = staff_client.patch(
            f"/api/inventory/work-orders/{work_order.id}/tasks/{step.id}/complete/",
            {"is_completed": True},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        assert resp.json()["is_timing"] is False
        assert 74 <= resp.json()["elapsed_seconds"] <= 77

        step.refresh_from_db()
        assert step.is_timing is False
        assert 74 <= step.elapsed_seconds <= 77

    def test_reopening_a_step_does_not_resume_its_clock(self, staff_client, work_order):
        step = work_order.task_completions.first()
        _task_timer(staff_client, work_order, step, "start")
        _rewind(step, 40)
        staff_client.patch(
            f"/api/inventory/work-orders/{work_order.id}/tasks/{step.id}/complete/",
            {"is_completed": True},
            format="json",
        )
        staff_client.patch(
            f"/api/inventory/work-orders/{work_order.id}/tasks/{step.id}/complete/",
            {"is_completed": False},
            format="json",
        )
        step.refresh_from_db()
        assert step.is_timing is False
        assert 39 <= step.elapsed_seconds <= 42

    def test_step_from_another_work_order_is_not_found(self, staff_client, work_order):
        other = WorkOrder.objects.create(maintenance_item=work_order.maintenance_item)
        foreign = WorkOrderTaskCompletion.objects.create(
            work_order=other, task_title="Not yours", task_order=0
        )
        resp = _task_timer(staff_client, work_order, foreign, "start")
        assert resp.status_code == 404


class TestCompletionRecordsTime:
    def test_completion_stops_every_clock(self, staff_client, work_order):
        step = work_order.task_completions.first()
        _timer(staff_client, work_order, "start")
        _task_timer(staff_client, work_order, step, "start")
        _rewind(work_order, 600)
        _rewind(step, 300)

        _complete(staff_client, work_order)

        step.refresh_from_db()
        assert work_order.is_timing is False
        assert step.is_timing is False
        assert 599 <= work_order.elapsed_seconds <= 602
        assert 299 <= step.elapsed_seconds <= 302

    def test_completion_writes_time_spent_minutes(self, staff_client, work_order):
        _timer(staff_client, work_order, "start")
        _rewind(work_order, 47 * 60)
        _complete(staff_client, work_order)

        log = MaintenanceLog.objects.get(work_order=work_order)
        assert log.time_spent_minutes == 47

    def test_completion_does_not_clobber_a_manual_entry(self, staff_client, work_order):
        # Somebody logged the time by hand before closing the WO.
        MaintenanceLog.objects.create(
            maintenance_item=work_order.maintenance_item,
            work_order=work_order,
            time_spent_minutes=90,
            notes="Hand-entered",
        )
        _timer(staff_client, work_order, "start")
        _rewind(work_order, 47 * 60)
        _complete(staff_client, work_order)

        log = MaintenanceLog.objects.get(work_order=work_order)
        assert log.time_spent_minutes == 90

    def test_untimed_work_order_leaves_time_spent_null(self, staff_client, work_order):
        _complete(staff_client, work_order)
        log = MaintenanceLog.objects.get(work_order=work_order)
        assert log.time_spent_minutes is None

    def test_reopening_keeps_the_accumulated_total(self, staff_client, work_order):
        _timer(staff_client, work_order, "start")
        _rewind(work_order, 300)
        _complete(staff_client, work_order)
        committed = work_order.elapsed_seconds

        resp = staff_client.patch(
            f"/api/inventory/work-orders/{work_order.id}/",
            {"status": WorkOrder.Status.IN_PROGRESS},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        work_order.refresh_from_db()
        # Reopening never auto-resumes — the tech restarts the clock by hand.
        assert work_order.elapsed_seconds == committed
        assert work_order.is_timing is False

    @pytest.mark.parametrize(
        "seconds,expected",
        [(0, None), (1, 1), (29, 1), (90, 2), (47 * 60, 47)],
    )
    def test_recorded_minutes_rounding(self, work_order, seconds, expected):
        work_order.elapsed_seconds = seconds
        # A clock that ran at all reports at least a minute: 0 would be
        # indistinguishable from "never timed".
        assert recorded_minutes(work_order) == expected


class TestPrintedSignOff:
    def test_blank_form_keeps_the_write_in(self, work_order):
        text = _pdf_text(generate_work_order_pdf(work_order))
        assert "Time Spent (min):" in text
        assert "min (est 30)" not in text

    def test_completed_form_prints_actual_against_estimate(self, staff_client, work_order):
        _timer(staff_client, work_order, "start")
        _rewind(work_order, 47 * 60)
        _complete(staff_client, work_order)

        text = _pdf_text(generate_work_order_pdf(work_order))
        assert "47 min (est 30)" in text

    def test_completed_but_untimed_form_keeps_the_write_in(self, staff_client, work_order):
        _complete(staff_client, work_order)
        text = _pdf_text(generate_work_order_pdf(work_order))
        assert "min (est 30)" not in text


class TestPermissionMatrix:
    def test_timer_actions_are_registered(self, settings):
        matrix = yaml.safe_load(
            (settings.BASE_DIR / "config" / "api_permission_matrix.yaml").read_text()
        )
        actions = matrix["views"]["inventory.views.WorkOrderViewSet"]["actions"]
        # Both new writes mirror complete_task — a volunteer may read a WO but
        # only staff / SIG admins may put time on one.
        for name in ("timer", "task_timer"):
            assert actions[name]["permission_classes"] == ["IsAuthenticatedOrStaffSigAdminWrite"]
        assert (
            actions["complete_task"]["permission_classes"] == actions["timer"]["permission_classes"]
        )
