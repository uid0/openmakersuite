"""
URL configuration for makerspace inventory management system.
"""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path

from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from auth_views import (
    create_test_invite_code,
    create_test_membership,
    login_user,
    logout_user,
    refresh_token,
    register_user,
)
from config.health import livez, readyz
from config.protected_media import media_access_check, serve_media
from electrical_circuits.urls import (
    asset_power_chain_urlpatterns as electrical_asset_power_chain_urls,
)
from electrical_circuits.urls import safety_urlpatterns as electrical_safety_urls

urlpatterns = [
    path("admin/", admin.site.urls),
    # Health probes (AC-11 liveness, AC-12 readiness). Liveness is dep-free;
    # readiness verifies database, cache, and Celery broker.
    path("api/health/livez/", livez, name="health-livez"),
    path("api/health/readyz/", readyz, name="health-readyz"),
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
    # nginx ``auth_request`` target for the vendor-paperwork media prefixes.
    # Not a browsing endpoint — see ``config.protected_media``.
    path("api/auth/media-access/", media_access_check, name="media-access-check"),
    path("api/auth/test-membership/", create_test_membership, name="test_membership"),
    path("api/auth/test-invite-code/", create_test_invite_code, name="test_invite_code"),
    # Passkey/WebAuthn endpoints
    path("auth/passkey/", include("passkeys.urls")),
    path("api/inventory/", include("inventory.urls")),
    path("api/membership/", include("membership.urls")),
    path("api/reorders/", include("reorder_queue.urls")),
    path("api/index-cards/", include("index_cards.urls")),
    path("api/dashboard/", include("dashboard.urls")),
    path("api/forgekey/", include("forgekey.urls")),
    path("api/lockers/", include("lockers.urls")),
    path("api/customization/", include("customization.urls")),
    path("api/location-checkins/", include("location_checkins.urls")),
    path("api/scanner/", include("scanner.urls")),
    path("api/preventive-maintenance/", include("preventive_maintenance.urls")),
    path("api/checklists/", include("checklists.urls")),
    path("api/donations/", include("donations.urls")),
    path("api/search/", include("search.urls")),
    path("api/notifications/", include("notifications.urls")),
    # Account device-management + "this wasn't me" revoke-all (notifications FP3).
    path("api/account/", include("notifications.account_urls")),
    path("api/screens/", include("screens.urls")),
    path("api/maker-boxes/", include("maker_boxes.urls")),
    path("api/vendors/", include("vendors.urls")),
    path("api/maintenance-orders/", include("maintenance_orders.urls")),
    path("api/electrical-circuits/", include("electrical_circuits.urls")),
    # Safety query endpoints (oms-b25 AC-1..AC-4). Mounted at the bare
    # /api/electrical/ and /api/assets/ prefixes so the URLs match the
    # AC paths exactly.
    path("api/electrical/", include(electrical_safety_urls)),
    path("api/assets/", include(electrical_asset_power_chain_urls)),
    path("api/loto/", include("loto.urls")),
    path("api/analytics/", include("analytics.urls")),
    path("api/climate/", include("climate.urls")),
    path("api/project-storage/", include("project_storage.urls")),
    path("api/storage-vision/", include("storage_vision.urls")),
    path("api/interlocks/", include("interlocks.urls")),
    path("api/accounting/", include("accounting.urls")),
    # Circuit-breaker health as a user-facing status board, so the web app
    # and ScanTTY can say which capability is down instead of failing silently.
    path("api/resilience/", include("resilience.urls")),
    # Flower proxy (superuser only)
    path("flower/", include("config.flower_urls")),
]

# Media. Registered UNCONDITIONALLY, not under ``if settings.DEBUG``: the
# vendor prefixes in ``config.protected_media`` must be gated wherever
# ``/media/`` is answered, and a rule that only exists in development is one
# nothing in CI can exercise. In production nginx answers these paths first (and
# applies the same prefix list via ``auth_request``), so this view is what runs
# for the development server, the test client, and any deployment without the
# nginx layer in front.
urlpatterns += [
    re_path(
        r"^%s(?P<path>.*)$" % settings.MEDIA_URL.lstrip("/"),
        serve_media,
        name="media",
    ),
]
