"""
URL configuration for reorder queue app.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ReorderRequestViewSet

router = DefaultRouter()
router.register(r'requests', ReorderRequestViewSet)

urlpatterns = [
    path('', include(router.urls)),
]
