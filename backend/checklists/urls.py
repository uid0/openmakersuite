"""
URLs for checklist API.
"""

from django.urls import include, path

from config.routers import ApiRouter

from .views import ChecklistCompletionViewSet, ChecklistViewSet

router = ApiRouter()
router.register(r"checklists", ChecklistViewSet, basename="checklist")
router.register(r"completions", ChecklistCompletionViewSet, basename="checklist-completion")

urlpatterns = [
    path("", include(router.urls)),
]
