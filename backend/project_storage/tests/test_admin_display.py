"""Admin column label for the project-storage stint status callable.

The django-upgrade 4.2 rewrite swapped ``fn.short_description = "..."`` for
``@admin.display(description="...")``; this locks the rendered label in place.
"""

from django.contrib import admin
from django.contrib.admin.utils import label_for_field

from project_storage.admin import ProjectStorageStintAdmin
from project_storage.models import ProjectStorageStint


class TestProjectStorageStintAdminLabels:
    def test_status_display_callable_is_labeled_status(self):
        assert ProjectStorageStintAdmin.status_display.short_description == "Status"

    def test_changelist_renders_the_same_label(self):
        model_admin = ProjectStorageStintAdmin(ProjectStorageStint, admin.site)
        label = label_for_field("status_display", ProjectStorageStint, model_admin=model_admin)
        assert label == "Status"
