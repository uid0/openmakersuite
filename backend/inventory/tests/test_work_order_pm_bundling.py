"""When SiteSettings.pm_auto_bundle_due_within_days > 0, creating a
WorkOrder against one MaintenanceItem should silently roll in every
other PM on the same asset that is also due (or overdue) within the
window. Completing the WO then closes every bundled item, not just
the primary.

Slice 1 of the cascade-PMs work — the data model + auto-bundle hook
land here; the UI grouping by item arrives in slice 2.
"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from customization.models import SiteSettings
from inventory.models import MaintenanceItem, MaintenanceLog, MaintenanceTask, WorkOrder
from inventory.tests.factories import AssetFactory

User = get_user_model()
pytestmark = pytest.mark.django_db


def _set_bundle_window(days: int) -> None:
    """Pin SiteSettings.pm_auto_bundle_due_within_days for the test."""
    s = SiteSettings.get()
    s.pm_auto_bundle_due_within_days = days
    s.save()


@pytest.fixture
def staff_user():
    return User.objects.create_user(username="warden", password="x", is_staff=True)


@pytest.fixture
def staff_client(staff_user):
    api = APIClient()
    api.force_authenticate(user=staff_user)
    return api


@pytest.fixture
def asset_with_two_pms():
    asset = AssetFactory()
    weekly = MaintenanceItem.objects.create(
        asset=asset,
        title="Weekly filter check",
        description="x",
        interval_days=7,
    )
    MaintenanceTask.objects.create(maintenance_item=weekly, order=0, title="Inspect filter housing")
    monthly = MaintenanceItem.objects.create(
        asset=asset,
        title="Monthly descale",
        description="x",
        interval_days=30,
    )
    MaintenanceTask.objects.create(maintenance_item=monthly, order=0, title="Run descale cycle")
    return asset, weekly, monthly


def _validate(api, wo):
    return api.post(
        f"/api/inventory/work-orders/{wo.id}/validate/",
        {
            "electrical_acknowledged": True,
            "loto_acknowledged": True,
            "required_fields_acknowledged": True,
        },
        format="json",
    )


def _create_wo(api, item):
    payload = {
        "maintenance_item": str(item.id),
        "due_date": (date.today() + timedelta(days=1)).isoformat(),
        "status": "open",
    }
    resp = api.post("/api/inventory/work-orders/", payload, format="json")
    assert resp.status_code == 201, resp.content
    return WorkOrder.objects.get(pk=resp.json()["id"])


class TestAutoBundleDisabled:
    def test_setting_zero_does_not_bundle(self, staff_client, asset_with_two_pms):
        _set_bundle_window(0)
        _, weekly, monthly = asset_with_two_pms

        wo = _create_wo(staff_client, weekly)

        # Bundle is empty — back-compat with existing deploys. The
        # DRF create path historically didn't materialise the primary
        # item's task_completions either (only ``generate_work_order``
        # does), so when the setting is off this stays at 0 to match
        # the legacy behaviour exactly.
        assert wo.additional_maintenance_items.count() == 0
        assert wo.task_completions.count() == 0


class TestAutoBundleEnabled:
    def test_window_7_bundles_overdue_sibling(self, staff_client, asset_with_two_pms):
        _set_bundle_window(7)
        _, weekly, monthly = asset_with_two_pms
        # Neither item has been done — both are overdue from creation,
        # so both should land in the bundle.

        wo = _create_wo(staff_client, weekly)

        bundled = list(wo.additional_maintenance_items.values_list("id", flat=True))
        assert monthly.id in bundled
        # Primary item is NOT also in additional_maintenance_items.
        assert weekly.id not in bundled

        # task_completions includes tasks from BOTH items.
        task_items = {tc.task.maintenance_item_id for tc in wo.task_completions.all()}
        assert weekly.id in task_items
        assert monthly.id in task_items

    def test_sibling_due_after_window_excluded(self, staff_client, asset_with_two_pms):
        _set_bundle_window(7)
        asset, weekly, monthly = asset_with_two_pms
        # Push monthly's last_completed_at to now so its next_due_at
        # lands 30 days out — outside the 7-day window.
        monthly.last_completed_at = timezone.now()
        monthly.save()

        wo = _create_wo(staff_client, weekly)
        assert wo.additional_maintenance_items.count() == 0

    def test_sibling_on_different_asset_not_bundled(
        self, staff_client, asset_with_two_pms, settings
    ):
        _set_bundle_window(7)
        _, weekly, _ = asset_with_two_pms
        # A PM on a DIFFERENT asset (also overdue) must NOT be rolled
        # into the bundle — same-asset is the contract.
        other_asset = AssetFactory()
        unrelated = MaintenanceItem.objects.create(
            asset=other_asset,
            title="Unrelated PM",
            description="x",
            interval_days=7,
        )
        wo = _create_wo(staff_client, weekly)
        bundled = list(wo.additional_maintenance_items.values_list("id", flat=True))
        assert unrelated.id not in bundled


class TestCompletionCascadesToEveryBundledItem:
    def test_close_writes_log_and_advances_last_completed_for_every_item(
        self, staff_client, asset_with_two_pms, settings
    ):
        _set_bundle_window(7)
        _, weekly, monthly = asset_with_two_pms
        wo = _create_wo(staff_client, weekly)
        assert _validate(staff_client, wo).status_code == 201

        resp = staff_client.patch(
            f"/api/inventory/work-orders/{wo.id}/",
            {"status": "completed"},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        wo.refresh_from_db()
        weekly.refresh_from_db()
        monthly.refresh_from_db()

        # Both items have a log row referencing this WO + each has
        # last_completed_at bumped to the WO's completion timestamp.
        weekly_log = MaintenanceLog.objects.get(work_order=wo, maintenance_item=weekly)
        monthly_log = MaintenanceLog.objects.get(work_order=wo, maintenance_item=monthly)
        assert weekly_log.completed_at == wo.completed_at
        assert monthly_log.completed_at == wo.completed_at
        assert weekly.last_completed_at == wo.completed_at
        assert monthly.last_completed_at == wo.completed_at
        assert weekly.is_overdue is False
        assert monthly.is_overdue is False

    def test_reopen_recomplete_does_not_double_log_any_item(
        self, staff_client, asset_with_two_pms, settings
    ):
        _set_bundle_window(7)
        _, weekly, monthly = asset_with_two_pms
        wo = _create_wo(staff_client, weekly)
        _validate(staff_client, wo)
        staff_client.patch(
            f"/api/inventory/work-orders/{wo.id}/",
            {"status": "completed"},
            format="json",
        )
        staff_client.patch(
            f"/api/inventory/work-orders/{wo.id}/",
            {"status": "open"},
            format="json",
        )
        staff_client.patch(
            f"/api/inventory/work-orders/{wo.id}/",
            {"status": "completed"},
            format="json",
        )

        # Still exactly one log per (wo, item) pair.
        assert MaintenanceLog.objects.filter(work_order=wo).count() == 2
