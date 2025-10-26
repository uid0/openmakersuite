"""Serializers for index card generation requests."""

from __future__ import annotations

from typing import List

from rest_framework import serializers


class IndexCardPreviewSerializer(serializers.Serializer):
    """Validate data for generating a preview card."""

    item_id = serializers.UUIDField(help_text="Inventory item identifier")
    blank_card = serializers.BooleanField(
        default=False,
        help_text="If True, generate blank card with only QR code for creative customization"
    )


class IndexCardBatchSerializer(serializers.Serializer):
    """Validate data for generating a batch of index cards."""

    item_ids = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=False,
        help_text="List of inventory item identifiers to include in the batch",
    )
    filename = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Optional file name for the generated PDF",
    )
    blank_cards = serializers.BooleanField(
        default=False,
        help_text="If True, generate blank cards with only QR codes for creative customization"
    )

    def validate_item_ids(self, value: List[str]) -> List[str]:
        """Ensure that each item identifier is unique."""

        if len(value) != len(set(value)):
            raise serializers.ValidationError("Duplicate item identifiers are not allowed.")
        return value
