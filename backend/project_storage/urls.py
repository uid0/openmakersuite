from rest_framework.routers import DefaultRouter

from .views import ProjectStorageStintViewSet

router = DefaultRouter()
router.register(r"stints", ProjectStorageStintViewSet, basename="project-storage-stints")

urlpatterns = router.urls
