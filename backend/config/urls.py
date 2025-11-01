"""
URL configuration for makerspace inventory management system.
"""

from auth_views import login_user, refresh_token, register_user
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from django.conf import settings
from django.conf.urls.static import static

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

# Serve media files during development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
