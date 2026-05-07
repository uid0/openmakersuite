"""
URL configuration for dashboard management API.
"""

from django.urls import path

from . import views

urlpatterns = [
    # Public endpoints (used by TV Dashboard)
    path("messages/", views.get_dashboard_messages, name="dashboard_messages"),
    path("inventory-summary/", views.get_inventory_summary, name="inventory_summary"),
    path("health/", views.dashboard_health, name="dashboard_health"),
    # Admin endpoints (authentication required)
    path("config/", views.get_dashboard_config, name="dashboard_config"),
    path("config/update/", views.update_dashboard_config, name="update_dashboard_config"),
    path("messages/add/", views.add_dashboard_message, name="add_dashboard_message"),
    # Widget endpoints (authentication required)
    path("widgets/", views.get_user_widgets, name="get_user_widgets"),
    path("widgets/save/", views.save_user_widgets, name="save_user_widgets"),
    path("widget-data/low-stock/", views.get_low_stock_data, name="get_low_stock_data"),
    path(
        "widget-data/pending-reorders/",
        views.get_pending_reorders_data,
        name="get_pending_reorders_data",
    ),
    path(
        "widget-data/asset-problems/",
        views.get_asset_problems_data,
        name="get_asset_problems_data",
    ),
    path("widget-data/qr-scans/", views.get_qr_scans_data, name="get_qr_scans_data"),
    path("widget-data/deliveries/", views.get_deliveries_data, name="get_deliveries_data"),
    # Audit feed (gh #359 / #334) — staff-only review surface across all
    # per-domain audit tables.
    path("audit-feed/", views.get_audit_feed, name="get_audit_feed"),
]
