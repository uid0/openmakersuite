from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from inventory.audit import record_event
from inventory.models import (
    LocationProblem,
    MaintenanceAuditEvent,
    MaintenanceItem,
    WorkOrder,
    WorkOrderValidation,
)
from inventory.tests.factories import AssetFactory, LocationFactory

pytestmark = pytest.mark.django_db

User = get_user_model()


def _staff_user(username: str) -> User:
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=f"{username}-password",
        is_staff=True,
    )


def _api_client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _maintenance_item():
    asset = AssetFactory()
    return MaintenanceItem.objects.create(
        asset=asset,
        title="Monthly inspection",
        description="Routine preventive maintenance",
        interval_days=7,
    )


def _work_order(*, status: str = WorkOrder.STATUS_OPEN, notes: str = "") -> WorkOrder:
    item = _maintenance_item()
    return WorkOrder.objects.create(
        maintenance_item=item,
        status=status,
        due_date=date.today() + timedelta(days=7),
        notes=notes,
    )


def _complete_validation(work_order: WorkOrder, user: User) -> WorkOrderValidation:
    return WorkOrderValidation.objects.create(
        work_order=work_order,
        validated_by=user,
        electrical_acknowledged=True,
        loto_acknowledged=True,
        required_fields_acknowledged=True,
    )


def test_wo_create_records_audit_event():
    user = _staff_user("wo-create-user")
    client = _api_client(user)
    maintenance_item = _maintenance_item()

    response = client.post(
        reverse("workorder-list"),
        {
            "maintenance_item": str(maintenance_item.id),
            "due_date": (date.today() + timedelta(days=3)).isoformat(),
            "notes": "Created from API test",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED, response.content

    work_order = WorkOrder.objects.get(pk=response.json()["id"])
    event = MaintenanceAuditEvent.objects.get(action=MaintenanceAuditEvent.ACTION_WO_CREATE)
    assert event.actor == user
    assert event.work_order == work_order
    assert event.location_problem is None
    assert event.metadata["maintenance_item_id"] == str(maintenance_item.id)


def test_wo_complete_records_audit_event_on_status_transition():
    user = _staff_user("wo-complete-user")
    client = _api_client(user)
    work_order = _work_order(status=WorkOrder.STATUS_OPEN)
    _complete_validation(work_order, user)

    response = client.patch(
        reverse("workorder-detail", kwargs={"pk": work_order.pk}),
        {"status": WorkOrder.STATUS_COMPLETED},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK, response.content

    event = MaintenanceAuditEvent.objects.get(action=MaintenanceAuditEvent.ACTION_WO_COMPLETE)
    assert event.actor == user
    assert event.work_order == work_order
    assert event.metadata["previous_status"] == WorkOrder.STATUS_OPEN


def test_wo_update_without_status_change_no_complete_audit():
    user = _staff_user("wo-update-user")
    client = _api_client(user)
    work_order = _work_order()

    response = client.patch(
        reverse("workorder-detail", kwargs={"pk": work_order.pk}),
        {"notes": "Updated notes only"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK, response.content
    assert (
        MaintenanceAuditEvent.objects.filter(
            action=MaintenanceAuditEvent.ACTION_WO_COMPLETE,
            work_order=work_order,
        ).count()
        == 0
    )


def test_wo_already_completed_update_no_duplicate_audit():
    user = _staff_user("wo-completed-user")
    client = _api_client(user)
    work_order = _work_order(status=WorkOrder.STATUS_COMPLETED)

    response = client.patch(
        reverse("workorder-detail", kwargs={"pk": work_order.pk}),
        {"status": WorkOrder.STATUS_COMPLETED},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK, response.content
    assert (
        MaintenanceAuditEvent.objects.filter(
            action=MaintenanceAuditEvent.ACTION_WO_COMPLETE,
            work_order=work_order,
        ).count()
        == 0
    )


def test_location_problem_resolve_records_audit_event():
    user = _staff_user("lp-resolve-user")
    client = _api_client(user)
    location = LocationFactory()
    problem = LocationProblem.objects.create(
        location=location,
        description="Standing water by the sink",
        severity=LocationProblem.SEVERITY_HIGH,
        status=LocationProblem.REPORTED,
    )

    response = client.post(
        reverse("locationproblem-resolve", kwargs={"pk": problem.pk}),
        {"resolution_notes": "swept and dried"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK, response.content

    problem.refresh_from_db()
    assert problem.status == LocationProblem.RESOLVED

    event = MaintenanceAuditEvent.objects.get(
        action=MaintenanceAuditEvent.ACTION_LOCATION_PROBLEM_RESOLVE
    )
    assert event.actor == user
    assert event.location_problem == problem
    assert event.notes == "swept and dried"
    assert event.metadata["new_status"] == LocationProblem.RESOLVED
    assert event.metadata["severity"] == problem.severity


def test_location_problem_resolve_invalid_status_no_audit():
    user = _staff_user("lp-invalid-user")
    client = _api_client(user)
    problem = LocationProblem.objects.create(
        location=LocationFactory(),
        description="Broken latch",
        status=LocationProblem.REPORTED,
    )

    response = client.post(
        reverse("locationproblem-resolve", kwargs={"pk": problem.pk}),
        {"status": "garbage"},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert (
        MaintenanceAuditEvent.objects.filter(
            action=MaintenanceAuditEvent.ACTION_LOCATION_PROBLEM_RESOLVE,
            location_problem=problem,
        ).count()
        == 0
    )


def test_record_event_with_anonymous_actor_creates_row_with_null_actor():
    work_order = _work_order()

    event = record_event(
        action=MaintenanceAuditEvent.ACTION_WO_CREATE,
        actor=None,
        work_order=work_order,
    )

    assert event.actor is None
    assert event.work_order == work_order


def test_record_event_accepts_either_entity_only():
    work_order = _work_order()

    event = record_event(
        action=MaintenanceAuditEvent.ACTION_WO_CREATE,
        work_order=work_order,
    )

    assert event.actor is None
    assert event.work_order == work_order
    assert event.location_problem is None
