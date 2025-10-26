"""URL routes for the index cards application."""

from django.urls import path

from .views import IndexCardBatchGenerateView, IndexCardPreviewView

app_name = "index_cards"

urlpatterns = [
    path("preview/", IndexCardPreviewView.as_view(), name="preview"),
    path("generate/", IndexCardBatchGenerateView.as_view(), name="generate"),
]
