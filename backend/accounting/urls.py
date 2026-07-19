from django.urls import include, path

from rest_framework.routers import DefaultRouter

from .views import (
    AccountViewSet,
    CommitteeSettlementView,
    CommitteeStatementView,
    TrialBalanceView,
)

router = DefaultRouter()
router.register(r"accounts", AccountViewSet, basename="account")

urlpatterns = [
    path("trial-balance/", TrialBalanceView.as_view(), name="trial-balance"),
    path(
        "committee-statement/",
        CommitteeStatementView.as_view(),
        name="committee-statement",
    ),
    path(
        "committee-settlement/",
        CommitteeSettlementView.as_view(),
        name="committee-settlement",
    ),
    path("", include(router.urls)),
]
