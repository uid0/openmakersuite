"""
URL configuration for makerspace inventory management system.
"""
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/token/", include("rest_framework.urls")),
    path("api/inventory/", include("inventory.urls")),
    path("api/reorders/", include("reorder_queue.urls")),
    path("api/index-cards/", include("index_cards.urls")),
]