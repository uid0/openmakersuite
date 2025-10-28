"""API views for inventory index card rendering."""

from __future__ import annotations

from typing import List

from django.shortcuts import get_object_or_404
from django.utils.encoding import iri_to_uri

from rest_framework import status
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from rest_framework.response import Response
from rest_framework.views import APIView

from inventory.models import InventoryItem

from .serializers import IndexCardBatchSerializer, IndexCardPreviewSerializer
from .services import IndexCardRenderer, build_preview_payload


class IndexCardPreviewView(APIView):
    """Provide a base64-encoded preview for a single inventory item."""

    permission_classes = [IsAuthenticatedOrReadOnly]

    def post(self, request, *args, **kwargs):
        serializer = IndexCardPreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        item = get_object_or_404(InventoryItem, id=serializer.validated_data["item_id"])
        blank_card = serializer.validated_data.get("blank_card", False)
        renderer = IndexCardRenderer(blank_cards=blank_card)
        payload = build_preview_payload(item, renderer, blank_card)

        return Response(payload, status=status.HTTP_200_OK)


class IndexCardBatchGenerateView(APIView):
    """Generate a PDF using Avery Template 5388 (3 cards per page, 5"×3" each)."""

    permission_classes = [IsAuthenticatedOrReadOnly]

    def post(self, request, *args, **kwargs):
        serializer = IndexCardBatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        validated_ids = serializer.validated_data["item_ids"]
        item_ids: List[str] = [str(value) for value in validated_ids]
        items = list(InventoryItem.objects.filter(id__in=validated_ids))

        if len(items) != len(validated_ids):
            missing = set(item_ids) - {str(item.id) for item in items}
            return Response(
                {
                    "detail": "Some requested inventory items were not found.",
                    "missing_ids": sorted(missing),
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        items.sort(key=lambda item: item_ids.index(str(item.id)))

        blank_cards = serializer.validated_data.get("blank_cards", False)
        renderer = IndexCardRenderer(blank_cards=blank_cards)
        generated = renderer.render_batch_to_storage(
            items, filename=serializer.validated_data.get("filename"), blank_cards=blank_cards
        )

        card_type = "blank" if blank_cards else "detailed"
        response_payload = {
            "file_path": generated.path,
            "file_url": iri_to_uri(generated.url),
            "absolute_path": generated.absolute_path,
            "count": len(items),
            "card_type": card_type,
        }
        return Response(response_payload, status=status.HTTP_201_CREATED)
