"""
URL configuration for makerspace inventory management system.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from auth_views import create_test_membership, login_user, logout_user, refresh_token, register_user

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path("api/token/", include("rest_framework.urls")),
    # Custom auth endpoints
    path("api/auth/register/", register_user, name="register"),
    path("api/auth/login/", login_user, name="login"),
    path("api/auth/logout/", logout_user, name="logout"),
    path("api/auth/refresh/", refresh_token, name="refresh"),
    path("api/auth/test-membership/", create_test_membership, name="test_membership"),
    # Passkey/WebAuthn endpoints
    path("auth/passkey/", include("passkeys.urls")),
    path("api/inventory/", include("inventory.urls")),
    path("api/membership/", include("membership.urls")),
    path("api/reorders/", include("reorder_queue.urls")),
    path("api/index-cards/", include("index_cards.urls")),
    path("api/dashboard/", include("dashboard.urls")),
    path("api/forgekey/", include("forgekey.urls")),
    path("api/customization/", include("customization.urls")),
    path("api/location-checkins/", include("location_checkins.urls")),
    path("api/checklists/", include("checklists.urls")),
    path("api/donations/", include("donations.urls")),
    path("api/search/", include("search.urls")),
    path("api/notifications/", include("notifications.urls")),
    path("api/screens/", include("screens.urls")),
    path("api/maker-boxes/", include("maker_boxes.urls")),
    path("api/vendors/", include("vendors.urls")),
    path("api/maintenance-orders/", include("maintenance_orders.urls")),
    path("api/electrical-circuits/", include("electrical_circuits.urls")),
    # Flower proxy (superuser only)
    path("flower/", include("config.flower_urls")),
]

# Serve media files during development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
