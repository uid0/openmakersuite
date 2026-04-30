"""Tests for the failed-task recovery surface (AC-31).

The Django admin at ``/admin/django_celery_results/taskresult/`` is the
documented staff surface for inspecting Celery task failures (status,
worker, traceback, args/kwargs). These tests pin the contract so that a
future refactor of ``backend/config/celery_admin.py`` cannot silently
hide failures from operators.
"""

from django.contrib import admin as django_admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory

import pytest
from django_celery_results.models import TaskResult

User = get_user_model()


@pytest.mark.unit
class TestCeleryTaskResultAdminRegistration:
    """The admin must be registered with the contract operators rely on."""

    def test_taskresult_admin_is_registered(self):
        assert TaskResult in django_admin.site._registry

    def test_admin_exposes_status_filter(self):
        site_admin = django_admin.site._registry[TaskResult]
        assert "status" in site_admin.list_filter, (
            "status filter is the path operators take to find FAILURE rows; "
            "removing it breaks the documented recovery flow."
        )

    def test_admin_displays_status_and_task_name(self):
        site_admin = django_admin.site._registry[TaskResult]
        # Display columns are decorated methods (`status_display`,
        # `task_name_display`) — accept either form.
        cols = set(site_admin.list_display)
        assert any(c in cols for c in ("status", "status_display"))
        assert any(c in cols for c in ("task_name", "task_name_display"))

    def test_admin_exposes_traceback_readonly(self):
        site_admin = django_admin.site._registry[TaskResult]
        assert "traceback" in site_admin.readonly_fields, (
            "Tracebacks must be visible (read-only) on the detail page so "
            "operators can diagnose failures without shell access."
        )

    def test_admin_exposes_search(self):
        site_admin = django_admin.site._registry[TaskResult]
        assert "task_name" in site_admin.search_fields
        assert "task_id" in site_admin.search_fields


@pytest.mark.django_db
class TestFailedTaskAdminListing:
    """A failed TaskResult is filterable to FAILURE through the admin.

    Uses the ChangeList directly instead of rendering the admin template —
    template rendering hits a Context.__copy__ AttributeError on Python
    3.14 unrelated to the admin contract under test.
    """

    def _changelist(self, qs_kwargs):
        admin_user = User.objects.create_superuser(
            username="ops%s" % id(qs_kwargs),
            email="o@example.com",
            password="x" * 24,
        )
        site_admin = django_admin.site._registry[TaskResult]
        request = RequestFactory().get("/admin/django_celery_results/taskresult/", qs_kwargs)
        request.user = admin_user
        return site_admin.get_changelist_instance(request)

    def test_failure_filter_narrows_to_failed_rows(self, db):
        TaskResult.objects.create(
            task_id="t-1",
            task_name="reorder_queue.tasks.send_webhook_notification",
            status="FAILURE",
            traceback="ConnectionError",
        )
        TaskResult.objects.create(
            task_id="t-2",
            task_name="vendors.flag_expiring_compliance",
            status="SUCCESS",
        )

        cl = self._changelist({"status": "FAILURE"})
        names = list(cl.queryset.values_list("task_name", flat=True))

        assert names == ["reorder_queue.tasks.send_webhook_notification"]

    def test_changelist_lists_all_statuses_when_unfiltered(self, db):
        TaskResult.objects.create(task_id="t-a", task_name="a", status="FAILURE")
        TaskResult.objects.create(task_id="t-b", task_name="b", status="SUCCESS")

        cl = self._changelist({})
        statuses = sorted(cl.queryset.values_list("status", flat=True))
        assert statuses == ["FAILURE", "SUCCESS"]

    def test_traceback_is_persisted_for_recovery(self, db):
        """Recovery requires the traceback to be queryable from the row."""
        TaskResult.objects.create(
            task_id="t-3",
            task_name="forgekey.tasks.send_mqtt_command",
            status="FAILURE",
            traceback="Traceback ...\nMQTT publish failed with code 7",
        )
        result = TaskResult.objects.get(task_id="t-3")
        assert "MQTT publish failed with code 7" in result.traceback
