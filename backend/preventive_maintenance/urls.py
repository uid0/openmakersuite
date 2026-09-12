from django.urls import include, path

from config.routers import ApiRouter

from .views import PMScheduleViewSet, PMServiceLogViewSet

app_name = "preventive_maintenance"

router = ApiRouter()
router.register(r"schedules", PMScheduleViewSet, basename="pm-schedule")
router.register(r"service-logs", PMServiceLogViewSet, basename="pm-service-log")

urlpatterns = [
    path("", include(router.urls)),
]
