"""Closing a problem report from the admin records when, and by whom.

The same class as ``reorder_queue.tests.test_admin_status_transitions``, found
by deriving it rather than by tripping over it: a status transition performed by
a hand-written ``queryset.update()`` beside the paths that already owned it.

``AssetProblem`` reaches ``resolved``/``closed`` by four routes, and three of
them stamp ``resolved_at`` and ``resolved_by`` together:

  * ``AssetViewSet.resolve_problem`` and ``AssetProblemViewSet.resolve`` — both
    accept ``closed`` explicitly and stamp both columns for it;
  * ``services.problem_auto_resolve.resolve_problems_for_work_order`` — the
    module every work-order completion path calls, for that reason.

``AssetProblemAdmin.mark_closed`` was the fourth, and it set ``status`` alone,
so a report closed from the changelist is shown closed with no resolution date
and no resolver — on the API's ``AssetProblemSerializer`` and in ScanTTY, which
decodes both fields. Its sibling ``mark_resolved`` stamped the date but not the
resolver.

Both checks drive the real admin action dispatch by POSTing to the changelist.
"""

from __future__ import annotations

from datetime import timedelta

from django.test import Client
from django.urls import reverse
from django.utils import timezone

import pytest

from inventory.models import AssetProblem
from inventory.tests.factories import AssetProblemFactory
from reorder_queue.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff():
    return UserFactory(is_staff=True, is_superuser=True, username="quinn")


@pytest.fixture
def admin_client(staff):
    client = Client()
    client.force_login(staff)
    return client


def run_admin_action(client, action, *objects):
    meta = AssetProblem._meta
    url = reverse(f"admin:{meta.app_label}_{meta.model_name}_changelist")
    response = client.post(
        url,
        {
            "action": action,
            "_selected_action": [str(obj.pk) for obj in objects],
            "index": "0",
        },
        follow=True,
    )
    assert response.status_code == 200
    return response


def test_closing_from_the_admin_records_when_and_by_whom(admin_client, staff):
    problem = AssetProblemFactory(status=AssetProblem.Status.REPORTED)

    run_admin_action(admin_client, "mark_closed", problem)

    problem.refresh_from_db()
    assert problem.status == AssetProblem.Status.CLOSED
    assert problem.resolved_at is not None
    assert problem.resolved_by == staff.username


def test_resolving_from_the_admin_records_who_resolved_it(admin_client, staff):
    """``mark_resolved`` stamped the date and left the resolver blank."""
    problem = AssetProblemFactory(status=AssetProblem.Status.REPORTED)

    run_admin_action(admin_client, "mark_resolved", problem)

    problem.refresh_from_db()
    assert problem.status == AssetProblem.Status.RESOLVED
    assert problem.resolved_at is not None
    assert problem.resolved_by == staff.username


def test_closing_keeps_the_stamp_an_earlier_resolution_already_carried(admin_client, staff):
    """A report resolved by somebody else keeps THEIR name and moment.

    Matches ``AssetProblemViewSet.resolve``'s ``if not problem.resolved_at``
    guard and ``resolve_problems_for_work_order``'s "already-resolved reports
    are left alone": closing an already-resolved report is a filing change, not
    a new resolution, and must not overwrite who did the work.
    """
    original_moment = timezone.now() - timedelta(days=3)
    problem = AssetProblemFactory(status=AssetProblem.Status.RESOLVED)
    problem.resolved_by = "dana"
    problem.resolved_at = original_moment
    problem.save()

    run_admin_action(admin_client, "mark_closed", problem)

    problem.refresh_from_db()
    assert problem.status == AssetProblem.Status.CLOSED
    assert problem.resolved_by == "dana"
    assert problem.resolved_at == original_moment


def test_closing_moves_the_updated_stamp(admin_client, staff):
    """``updated_at`` is ``auto_now``, which a ``queryset.update()`` never moves."""
    problem = AssetProblemFactory(status=AssetProblem.Status.REPORTED)
    before = AssetProblem.objects.get(pk=problem.pk).updated_at

    run_admin_action(admin_client, "mark_closed", problem)

    problem.refresh_from_db()
    assert problem.updated_at > before


def test_mark_resolved_still_leaves_a_closed_report_alone(admin_client, staff):
    """The REPORTED precondition ``mark_resolved`` already carried still holds."""
    problem = AssetProblemFactory(status=AssetProblem.Status.CLOSED)

    run_admin_action(admin_client, "mark_resolved", problem)

    problem.refresh_from_db()
    assert problem.status == AssetProblem.Status.CLOSED
