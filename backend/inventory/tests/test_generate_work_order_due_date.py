"""Regression: generate_work_order must accept a client-supplied due_date (BACKEND-18).

The action passed the client's ``due_date`` string straight into
``WorkOrder.objects.create()``. The row persisted fine, but the *in-memory* instance
kept a ``str``, so ``WorkOrderSerializer``'s ``is_overdue``
(``WorkOrder.is_overdue``, inventory/models/maintenance.py) compared a ``date``
against a ``str`` and raised ``TypeError`` — a 500 raised AFTER the work order had
already been committed, so a retrying operator silently accumulated duplicates.

ScanTTY is the only client that fills the ``due_date`` field, which is why the web app
never tripped it: MaintenanceDashboard.tsx calls ``generateWorkOrder(item.id)`` with no
body, and both pre-existing endpoint tests post ``{}``.
"""

from __future__ import annotations

import datetime

from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.crypto import get_random_string

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from inventory.models import MaintenanceItem, WorkOrder
from inventory.tests.factories import AssetFactory

User = get_user_model()

pytestmark = pytest.mark.django_db

URL = "/api/inventory/maintenance-items/{}/generate_work_order/"


def _staff_client():
    user = User.objects.create_user(
        username=f"staff_{get_random_string(6)}",
        email="staff@example.com",
        password=get_random_string(24),
        is_staff=True,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


def _pm_item(**kwargs) -> MaintenanceItem:
    return MaintenanceItem.objects.create(
        asset=AssetFactory(),
        title="Monthly way-oil service",
        interval_days=30,
        **kwargs,
    )


class TestGenerateWorkOrderDueDate:
    def test_client_supplied_due_date_is_accepted(self):
        """The documented ``due_date`` body field must not 500 (BACKEND-18)."""
        client, _ = _staff_client()
        item = _pm_item()
        future = timezone.now().date() + datetime.timedelta(days=30)

        resp = client.post(URL.format(item.id), {"due_date": future.isoformat()}, format="json")

        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        assert resp.data["due_date"] == future.isoformat()
        assert resp.data["is_overdue"] is False

    def test_due_date_reaches_the_instance_as_a_date_not_a_string(self):
        """The mechanism: a str on the instance is what broke ``is_overdue``."""
        client, _ = _staff_client()
        item = _pm_item()

        resp = client.post(URL.format(item.id), {"due_date": "2020-01-01"}, format="json")

        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        wo = WorkOrder.objects.get(id=resp.data["id"])
        assert wo.due_date == datetime.date(2020, 1, 1)
        # a past due date on an open job: the property must evaluate, not raise
        assert resp.data["is_overdue"] is True

    def test_malformed_due_date_is_a_400_and_creates_nothing(self):
        """A bad date must be rejected BEFORE the work order is committed."""
        client, _ = _staff_client()
        item = _pm_item()

        resp = client.post(URL.format(item.id), {"due_date": "09/01/2026"}, format="json")

        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.data
        assert not WorkOrder.objects.filter(maintenance_item=item).exists()

    def test_no_due_date_falls_back_to_next_due_at(self):
        """The path the web app and the existing tests take must be unchanged."""
        client, _ = _staff_client()
        item = _pm_item(last_completed_at=timezone.now() - datetime.timedelta(days=10))
        expected = item.next_due_at.date()

        resp = client.post(URL.format(item.id), {}, format="json")

        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        assert resp.data["due_date"] == expected.isoformat()
        assert WorkOrder.objects.get(id=resp.data["id"]).due_date == expected

    def test_empty_due_date_falls_back_to_next_due_at(self):
        """ScanTTY omits a blank field, but an explicit "" must behave the same."""
        client, _ = _staff_client()
        item = _pm_item(last_completed_at=timezone.now() - datetime.timedelta(days=10))
        expected = item.next_due_at.date()

        resp = client.post(URL.format(item.id), {"due_date": ""}, format="json")

        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        assert resp.data["due_date"] == expected.isoformat()

    def test_no_due_date_and_no_schedule_leaves_it_unset(self):
        """A PM item that has never been completed still generates a work order."""
        client, _ = _staff_client()
        item = _pm_item()

        resp = client.post(URL.format(item.id), {}, format="json")

        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        assert resp.data["due_date"] is None
