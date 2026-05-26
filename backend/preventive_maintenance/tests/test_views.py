"""End-to-end tests for the PM viewsets + board endpoint."""

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

import pytest
from rest_framework import status

from inventory.tests.factories import AssetFactory, LocationFactory
from preventive_maintenance.models import PMSchedule, PMServiceLog

pytestmark = pytest.mark.django_db


def _board(api_client):
    return api_client.get(reverse("preventive_maintenance:pm-schedule-board"))


class TestPMBoardEndpoint:
    def test_anonymous_can_read_board(self, api_client):
        # Inkplate kiosk hits this without a JWT — must return 200.
        response = _board(api_client)
        assert response.status_code == status.HTTP_200_OK

    def test_empty_board(self, api_client):
        response = _board(api_client)
        assert response.data["count"] == 0
        assert response.data["schedules"] == []

    def test_board_returns_each_active_schedule(self, api_client):
        loc = LocationFactory()
        asset = AssetFactory(location=loc)
        schedule = PMSchedule.objects.create(
            asset=asset, task_name="Replace HEPA filter", interval_days=90
        )
        PMServiceLog.objects.create(
            schedule=schedule, performed_at=timezone.now() - timedelta(days=10)
        )
        response = _board(api_client)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        entry = response.data["schedules"][0]
        assert entry["asset_name"] == asset.name
        assert entry["location_name"] == loc.name
        assert entry["task_name"] == "Replace HEPA filter"
        assert entry["interval_days"] == 90
        assert entry["days_since_last"] == 10
        assert entry["days_until_due"] == 80
        assert entry["status"] == "ok"

    def test_board_excludes_inactive(self, api_client):
        asset = AssetFactory()
        PMSchedule.objects.create(asset=asset, task_name="A", interval_days=30, is_active=True)
        PMSchedule.objects.create(asset=asset, task_name="B", interval_days=30, is_active=False)
        response = _board(api_client)
        assert response.data["count"] == 1
        assert response.data["schedules"][0]["task_name"] == "A"

    def test_board_sorted_overdue_first(self, api_client):
        a1 = AssetFactory()
        a2 = AssetFactory()
        a3 = AssetFactory()
        s_ok = PMSchedule.objects.create(asset=a1, task_name="ok task", interval_days=30)
        s_over = PMSchedule.objects.create(asset=a2, task_name="overdue task", interval_days=30)
        s_warn = PMSchedule.objects.create(asset=a3, task_name="warning task", interval_days=100)
        PMServiceLog.objects.create(schedule=s_ok, performed_at=timezone.now())
        PMServiceLog.objects.create(
            schedule=s_over, performed_at=timezone.now() - timedelta(days=45)
        )
        PMServiceLog.objects.create(
            schedule=s_warn, performed_at=timezone.now() - timedelta(days=85)
        )
        response = _board(api_client)
        statuses = [e["status"] for e in response.data["schedules"]]
        # overdue then warning then ok
        assert statuses == ["overdue", "warning", "ok"]


@pytest.mark.integration
class TestPMScheduleWrites:
    def test_anonymous_cannot_create_schedule(self, api_client):
        asset = AssetFactory()
        url = reverse("preventive_maintenance:pm-schedule-list")
        response = api_client.post(
            url,
            data={"asset": str(asset.id), "task_name": "Replace filter", "interval_days": 30},
            format="json",
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_staff_can_create_schedule(self, api_client, admin_user):
        api_client.force_authenticate(user=admin_user)
        asset = AssetFactory()
        url = reverse("preventive_maintenance:pm-schedule-list")
        response = api_client.post(
            url,
            data={"asset": str(asset.id), "task_name": "Replace filter", "interval_days": 30},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert PMSchedule.objects.filter(task_name="Replace filter").exists()

    def test_log_service_action_records_now(self, api_client, admin_user):
        api_client.force_authenticate(user=admin_user)
        asset = AssetFactory()
        schedule = PMSchedule.objects.create(
            asset=asset, task_name="Replace filter", interval_days=30
        )
        url = reverse("preventive_maintenance:pm-schedule-log-service", kwargs={"pk": schedule.id})
        response = api_client.post(url, data={"notes": "Done"}, format="json")
        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert PMServiceLog.objects.filter(schedule=schedule).count() == 1
        log = PMServiceLog.objects.get(schedule=schedule)
        assert log.notes == "Done"
        assert log.performed_by_id == admin_user.id
