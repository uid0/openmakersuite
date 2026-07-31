from rest_framework.routers import DefaultRouter

from .views import ProjectStorageStintViewSet, StorageSlotViewSet

router = DefaultRouter()
router.register(r"stints", ProjectStorageStintViewSet, basename="project-storage-stints")
router.register(r"slots", StorageSlotViewSet, basename="project-storage-slots")

urlpatterns = router.urls
