"""
URL configuration for makerspace inventory management system.
"""
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from auth_views import register_user, login_user, refresh_token

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/token/", include("rest_framework.urls")),
    # Custom auth endpoints
    path("api/auth/register/", register_user, name="register"),
    path("api/auth/login/", login_user, name="login"),
    path("api/auth/refresh/", refresh_token, name="refresh"),
    path("api/inventory/", include("inventory.urls")),
    path("api/reorders/", include("reorder_queue.urls")),
    path("api/index-cards/", include("index_cards.urls")),
]