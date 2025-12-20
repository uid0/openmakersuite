"""
URL configuration for membership and SIG management API.
"""

from django.urls import include, path

from rest_framework.routers import DefaultRouter

from .views import (
    SIGAdminViewSet,
    SIGMemberViewSet,
    SIGViewSet,
    UserProfileViewSet,
    change_password,
)

router = DefaultRouter()
router.register(r"sigs", SIGViewSet, basename="sig")
router.register(r"sigs/(?P<sig_pk>\d+)/members", SIGMemberViewSet, basename="sig-member")
router.register(r"sig-admins", SIGAdminViewSet, basename="sig-admin")
router.register(r"profile", UserProfileViewSet, basename="profile")

urlpatterns = [
    path("change-password/", change_password, name="change-password"),
    path("", include(router.urls)),
]
