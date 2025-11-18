"""
URLs for checklist API.
"""

from django.urls import include, path

from rest_framework.routers import DefaultRouter

from .views import ChecklistCompletionViewSet, ChecklistViewSet

router = DefaultRouter()
router.register(r"checklists", ChecklistViewSet, basename="checklist")
router.register(r"completions", ChecklistCompletionViewSet, basename="checklist-completion")

urlpatterns = [
    path("", include(router.urls)),
]
