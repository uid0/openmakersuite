"""Admin column labels for the PM schedule display callables.

The django-upgrade 4.2 rewrite swapped ``fn.short_description = "..."`` for
``@admin.display(description="...")``; these lock the rendered labels in place.
"""

from django.contrib import admin
from django.contrib.admin.utils import label_for_field

from preventive_maintenance.admin import PMScheduleAdmin
from preventive_maintenance.models import PMSchedule


class TestPMScheduleAdminLabels:
    def test_status_callable_is_labeled_status(self):
        assert PMScheduleAdmin._status.short_description == "Status"

    def test_days_since_callable_is_labeled_last_service(self):
        assert PMScheduleAdmin._days_since.short_description == "Last service"

    def test_changelist_renders_the_same_labels(self):
        model_admin = PMScheduleAdmin(PMSchedule, admin.site)
        assert label_for_field("_status", PMSchedule, model_admin=model_admin) == "Status"
        assert label_for_field("_days_since", PMSchedule, model_admin=model_admin) == "Last service"
