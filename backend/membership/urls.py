"""
URL configuration for membership and SIG management API.
"""

from django.urls import include, path

from rest_framework.routers import DefaultRouter

from .views import (
    CommitteeViewSet,
    SIGAdminViewSet,
    SIGMemberViewSet,
    SIGViewSet,
    UserProfileViewSet,
    change_password,
    register_user_with_token,
    validate_registration_token,
)

router = DefaultRouter()
router.register(r"committees", CommitteeViewSet, basename="committee")
router.register(r"sigs", SIGViewSet, basename="sig")
router.register(r"sigs/(?P<sig_pk>\d+)/members", SIGMemberViewSet, basename="sig-member")
router.register(r"sig-admins", SIGAdminViewSet, basename="sig-admin")
router.register(r"profile", UserProfileViewSet, basename="profile")

urlpatterns = [
    path("change-password/", change_password, name="change-password"),
    path(
        "register/validate-token/",
        validate_registration_token,
        name="validate-registration-token",
    ),
    path(
        "register/complete/",
        register_user_with_token,
        name="register-user-with-token",
    ),
    path("", include(router.urls)),
]
