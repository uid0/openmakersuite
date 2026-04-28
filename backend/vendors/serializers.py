"""Serializers for the vendors API."""

from rest_framework import serializers

from .models import Vendor


class VendorSerializer(serializers.ModelSerializer):
    """Serializer for third-party service vendors."""

    vendor_kind_display = serializers.CharField(source="get_vendor_kind_display", read_only=True)
    tdlr_is_expired = serializers.BooleanField(read_only=True)
    coi_is_expired = serializers.BooleanField(read_only=True)

    class Meta:
        model = Vendor
        fields = [
            "id",
            "name",
            "vendor_kind",
            "vendor_kind_display",
            "contact_name",
            "phone",
            "email",
            "website",
            "address",
            "tdlr_license_number",
            "tdlr_license_expires_at",
            "tdlr_is_expired",
            "coi_provider",
            "coi_policy_number",
            "coi_expires_at",
            "coi_is_expired",
            "notes",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
