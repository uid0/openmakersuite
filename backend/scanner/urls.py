from django.urls import path

from .views import dispatch_scan

app_name = "scanner"

urlpatterns = [
    path("dispatch/", dispatch_scan, name="dispatch"),
]
