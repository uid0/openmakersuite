"""
URL configuration for Flower proxy.
"""

from django.urls import path, re_path

from .flower_proxy import flower_proxy

urlpatterns = [
    re_path(r"^.*$", flower_proxy, name="flower-proxy"),
]


