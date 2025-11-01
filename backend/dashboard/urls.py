"""
URL configuration for dashboard management API.
"""

from django.urls import path
from . import views

urlpatterns = [
    # Public endpoints (used by TV Dashboard)
    path('messages/', views.get_dashboard_messages, name='dashboard_messages'),
    path('health/', views.dashboard_health, name='dashboard_health'),
    
    # Admin endpoints (authentication required)
    path('config/', views.get_dashboard_config, name='dashboard_config'),
    path('config/update/', views.update_dashboard_config, name='update_dashboard_config'),
    path('messages/add/', views.add_dashboard_message, name='add_dashboard_message'),
]
