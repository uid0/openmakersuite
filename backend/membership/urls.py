"""
URL configuration for membership and SIG management API.
"""

from django.urls import include, path

from rest_framework.routers import DefaultRouter

from .views import (
    InviteCodeViewSet,
    SIGAdminViewSet,
    SIGMemberViewSet,
    SIGViewSet,
    UserProfileViewSet,
    change_password,
    invite_code_preview,
    redeem_invite_code,
    register_user_with_token,
    validate_registration_token,
)

router = DefaultRouter()
router.register(r"sigs", SIGViewSet, basename="sig")
router.register(r"sigs/(?P<sig_pk>\d+)/members", SIGMemberViewSet, basename="sig-member")
router.register(r"sig-admins", SIGAdminViewSet, basename="sig-admin")
router.register(r"profile", UserProfileViewSet, basename="profile")
router.register(r"invite-codes", InviteCodeViewSet, basename="invite-code")

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
    path("invite/preview/", invite_code_preview, name="invite-preview"),
    path("invite/redeem/", redeem_invite_code, name="invite-redeem"),
    path("", include(router.urls)),
]
