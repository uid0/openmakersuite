from django.urls import include, path

from rest_framework.routers import DefaultRouter

from .views import PMScheduleViewSet, PMServiceLogViewSet

app_name = "preventive_maintenance"

router = DefaultRouter()
router.register(r"schedules", PMScheduleViewSet, basename="pm-schedule")
router.register(r"service-logs", PMServiceLogViewSet, basename="pm-service-log")

urlpatterns = [
    path("", include(router.urls)),
]
