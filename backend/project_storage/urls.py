from django.urls import path

from rest_framework.routers import DefaultRouter

from .views import (
    ProjectStorageStintViewSet,
    StorageAssignmentViewSet,
    StorageOverviewView,
    StorageSlotViewSet,
)

router = DefaultRouter()
router.register(r"stints", ProjectStorageStintViewSet, basename="project-storage-stints")
router.register(r"slots", StorageSlotViewSet, basename="project-storage-slots")
router.register(r"assignments", StorageAssignmentViewSet, basename="project-storage-assignments")

# The overview spans both occupancy tables and every rack, so it hangs off the
# app root rather than under one of the collections above — it isn't a view of
# slots or of assignments, it's the view of the racking.
urlpatterns = [
    path("overview/", StorageOverviewView.as_view(), name="project-storage-overview"),
] + router.urls
