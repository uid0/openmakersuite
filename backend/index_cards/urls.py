"""URL routes for the index cards application."""

from django.urls import path

from .views import (
    IndexCardBatchGenerateView,
    IndexCardPreviewView,
    TestSheetGenerateView,
)

app_name = "index_cards"

urlpatterns = [
    path("preview/", IndexCardPreviewView.as_view(), name="preview"),
    path("generate/", IndexCardBatchGenerateView.as_view(), name="generate"),
    path("test-sheet/", TestSheetGenerateView.as_view(), name="test_sheet"),
]
