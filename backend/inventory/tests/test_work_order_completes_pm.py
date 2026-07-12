"""When a WorkOrder transitions to ``completed``, the linked MaintenanceItem
should look "done": ``last_completed_at`` advances, ``is_overdue`` clears,
and a MaintenanceLog row points back at the WO.

Before this fix the only path that wrote a MaintenanceLog was the
explicit "Log maintenance" button (``MaintenanceItemViewSet.complete``).
An operator who only closed work orders saw the PM keep nagging — see
the Water Fountain "Replace Water Filter" PM on 2026-06-12.
"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model

import pytest
from rest_framework.test import APIClient

from inventory.models import MaintenanceItem, MaintenanceLog, WorkOrder
from inventory.tests.factories import AssetFactory

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_user():
    return User.objects.create_user(username="warden", password="x", is_staff=True)


@pytest.fixture
def staff_client(staff_user):
    api = APIClient()
    api.force_authenticate(user=staff_user)
    return api


@pytest.fixture
def pm_item():
    asset = AssetFactory()
    return MaintenanceItem.objects.create(
        asset=asset,
        title="Replace water filter",
        description="Every 330 days",
        interval_days=330,
    )


def _validate(api, wo):
    """Slip the AC-3 validation gate so the WO can be marked complete."""
    return api.post(
        f"/api/inventory/work-orders/{wo.id}/validate/",
        {
            "electrical_acknowledged": True,
            "loto_acknowledged": True,
            "required_fields_acknowledged": True,
        },
        format="json",
    )


def _create_wo(api, item, *, status=WorkOrder.Status.OPEN):
    payload = {
        "maintenance_item": str(item.id),
        "due_date": (date.today() + timedelta(days=7)).isoformat(),
        "status": status,
    }
    resp = api.post("/api/inventory/work-orders/", payload, format="json")
    assert resp.status_code == 201, resp.content
    return WorkOrder.objects.get(pk=resp.json()["id"])


class TestWorkOrderClosesPM:
    def test_completing_a_wo_writes_log_and_advances_last_completed_at(self, staff_client, pm_item):
        # PM has never been done — overdue from the start.
        assert pm_item.last_completed_at is None
        assert pm_item.is_overdue is True

        wo = _create_wo(staff_client, pm_item)
        assert _validate(staff_client, wo).status_code == 201

        resp = staff_client.patch(
            f"/api/inventory/work-orders/{wo.id}/",
            {"status": WorkOrder.Status.COMPLETED},
            format="json",
        )
        assert resp.status_code == 200, resp.content

        wo.refresh_from_db()
        pm_item.refresh_from_db()

        log = MaintenanceLog.objects.get(work_order=wo)
        assert log.maintenance_item_id == pm_item.id
        assert log.completed_at == wo.completed_at

        # Last-completed bumps to the WO's completion timestamp; PM clears
        # the overdue flag and reports a next-due ~330 days out.
        assert pm_item.last_completed_at == wo.completed_at
        assert pm_item.is_overdue is False
        assert pm_item.next_due_at is not None

    def test_reopen_then_recomplete_does_not_double_log(self, staff_client, pm_item):
        wo = _create_wo(staff_client, pm_item)
        _validate(staff_client, wo)
        staff_client.patch(
            f"/api/inventory/work-orders/{wo.id}/",
            {"status": WorkOrder.Status.COMPLETED},
            format="json",
        )
        # Reopen.
        staff_client.patch(
            f"/api/inventory/work-orders/{wo.id}/",
            {"status": WorkOrder.Status.OPEN},
            format="json",
        )
        # Re-complete.
        staff_client.patch(
            f"/api/inventory/work-orders/{wo.id}/",
            {"status": WorkOrder.Status.COMPLETED},
            format="json",
        )

        # Still exactly one MaintenanceLog for this WO — the second
        # transition no-ops because the dedup FK already finds a row.
        assert MaintenanceLog.objects.filter(work_order=wo).count() == 1

    def test_reopening_a_completed_wo_does_not_regress_last_completed_at(
        self, staff_client, pm_item
    ):
        wo = _create_wo(staff_client, pm_item)
        _validate(staff_client, wo)
        staff_client.patch(
            f"/api/inventory/work-orders/{wo.id}/",
            {"status": WorkOrder.Status.COMPLETED},
            format="json",
        )
        pm_item.refresh_from_db()
        first_completed_at = pm_item.last_completed_at
        assert first_completed_at is not None

        # Reopen — last_completed_at stays pinned (the historical
        # completion is the last time the work was *actually* done).
        staff_client.patch(
            f"/api/inventory/work-orders/{wo.id}/",
            {"status": WorkOrder.Status.OPEN},
            format="json",
        )
        pm_item.refresh_from_db()
        assert pm_item.last_completed_at == first_completed_at

    def test_sync_helper_no_ops_without_maintenance_item(self):
        # Defense-in-depth: the helper guards against a missing
        # maintenance_item even though the DB constraint should make
        # this unreachable. Build an in-memory WO with no FK set.
        from inventory.views import WorkOrderViewSet

        wo = WorkOrder(status=WorkOrder.Status.COMPLETED, maintenance_item=None)
        WorkOrderViewSet._sync_maintenance_item_completion(wo, actor=None)
        # Nothing was written.
        assert MaintenanceLog.objects.count() == 0

    def test_open_wo_no_log(self, staff_client, pm_item):
        # An open WO doesn't write a log even though the FK is set.
        wo = _create_wo(staff_client, pm_item, status=WorkOrder.Status.OPEN)
        assert MaintenanceLog.objects.filter(work_order=wo).count() == 0
        pm_item.refresh_from_db()
        assert pm_item.last_completed_at is None
