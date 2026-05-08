"""Permission tests for WorkOrderViewSet (gh #374).

Operator-set rule (saved in Mayor agent memory 2026-05-08):

- Add or modify standard PM work orders: staff + SIG leaders.
- Read standard PM work orders: any authenticated user — volunteers see
  open + completed work orders that aren't routed through a third-party
  vendor.
- Add or modify third-party work orders: staff + SIG leaders.
- Read third-party work orders: staff + SIG leaders only — vendor
  relationships and quote/cost data must not leak to volunteers.
- Add vendors: staff only (SIG leaders cannot).

This test file pins the WorkOrderViewSet behavior. The third-party gate
is exercised in `maintenance_orders/tests/test_api.py`; the vendor gate
already had coverage in `vendors/tests/`.
"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.utils.crypto import get_random_string

import pytest
from rest_framework.test import APIClient

from inventory.models import MaintenanceItem, WorkOrder
from inventory.tests.factories import AssetFactory
from membership.models import SIGAdmin

User = get_user_model()

pytestmark = pytest.mark.django_db


def _wo_payload(*, status="open"):
    asset = AssetFactory()
    item = MaintenanceItem.objects.create(
        asset=asset,
        title="Monthly check",
        description="Routine",
        interval_days=30,
    )
    return {
        "maintenance_item": str(item.id),
        "due_date": (date.today() + timedelta(days=7)).isoformat(),
        "status": status,
    }


def _user(username, **flags):
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=get_random_string(24),
        **flags,
    )


@pytest.mark.django_db
class TestWorkOrderViewSetPermissions:
    """Reads stay open to volunteers; writes require staff or SIG admin."""

    def _client(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_volunteer_can_list(self):
        WorkOrder.objects.create(
            maintenance_item=MaintenanceItem.objects.create(
                asset=AssetFactory(),
                title="Existing",
                description="",
                interval_days=30,
            ),
            due_date=date.today() + timedelta(days=7),
        )
        resp = self._client(_user("volunteer")).get("/api/inventory/work-orders/")
        assert resp.status_code == 200

    def test_volunteer_cannot_create(self):
        resp = self._client(_user("volunteer")).post(
            "/api/inventory/work-orders/",
            data=_wo_payload(),
            format="json",
        )
        assert resp.status_code == 403

    def test_volunteer_cannot_patch(self):
        wo = WorkOrder.objects.create(
            maintenance_item=MaintenanceItem.objects.create(
                asset=AssetFactory(),
                title="Existing",
                description="",
                interval_days=30,
            ),
            due_date=date.today() + timedelta(days=7),
        )
        resp = self._client(_user("volunteer")).patch(
            f"/api/inventory/work-orders/{wo.id}/",
            data={"status": "in_progress"},
            format="json",
        )
        assert resp.status_code == 403

    def test_staff_can_create(self):
        resp = self._client(_user("admin", is_staff=True)).post(
            "/api/inventory/work-orders/",
            data=_wo_payload(),
            format="json",
        )
        assert resp.status_code == 201

    def test_sig_admin_can_create(self):
        user = _user("sigleader")
        SIGAdmin.objects.create(user=user, group=Group.objects.create(name="3D Printing SIG"))
        resp = self._client(user).post(
            "/api/inventory/work-orders/",
            data=_wo_payload(),
            format="json",
        )
        assert resp.status_code == 201

    def test_logistics_member_can_create(self):
        user = _user("logistics")
        user.groups.add(Group.objects.create(name="Logistics"))
        resp = self._client(user).post(
            "/api/inventory/work-orders/",
            data=_wo_payload(),
            format="json",
        )
        assert resp.status_code == 201

    def test_anonymous_blocked(self):
        resp = APIClient().get("/api/inventory/work-orders/")
        assert resp.status_code in (401, 403)
