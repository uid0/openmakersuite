from django.urls import include, path

from rest_framework.routers import DefaultRouter

from .views import AccountViewSet, TrialBalanceView

router = DefaultRouter()
router.register(r"accounts", AccountViewSet, basename="account")

urlpatterns = [
    path("trial-balance/", TrialBalanceView.as_view(), name="trial-balance"),
    path("", include(router.urls)),
]
