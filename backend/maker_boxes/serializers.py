"""Serializers for the maker box API."""

from __future__ import annotations

from rest_framework import serializers

from .models import MakerBox


class MakerBoxSerializer(serializers.ModelSerializer):
    display_name = serializers.CharField(read_only=True)

    class Meta:
        model = MakerBox
        fields = [
            "id",
            "bin_id",
            "assigned_username",
            "first_name",
            "last_name",
            "email",
            "display_name",
            "assigned_at",
            "expires_at",
            "last_verified_at",
            "status",
            "identity_source",
            "conversion_completed_at",
            "paid_at",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["last_verified_at", "created_at", "updated_at"]


class ScanRequestSerializer(serializers.Serializer):
    bin_id = serializers.CharField(max_length=20)
    username = serializers.CharField(max_length=64)


class ScanResponseSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["valid", "grace", "expired", "unknown"])
    bin_id = serializers.CharField()
    username = serializers.CharField()
    first_name = serializers.CharField(allow_blank=True)
    last_name = serializers.CharField(allow_blank=True)
    email = serializers.EmailField(allow_blank=True)
    expires_at = serializers.DateTimeField(allow_null=True)
    days_remaining = serializers.IntegerField(allow_null=True)


class ManualLabelRequestSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=64)
    first_name = serializers.CharField(max_length=64, allow_blank=True, required=False, default="")
    last_name = serializers.CharField(max_length=64, allow_blank=True, required=False, default="")


# ---------------------------------------------------------------------------
# Pre-conversion (PR 2)
# ---------------------------------------------------------------------------

# The pre-conversion screen accepts either a badge number (digits) or a
# WHMCS username (alphanumeric). The resolver decides which backend to
# call — the request shape is the same for both, hence ``query`` rather
# than ``username`` / ``badge``.


class LookupRequestSerializer(serializers.Serializer):
    query = serializers.CharField(max_length=64)


class LookupResponseSerializer(serializers.Serializer):
    """Result of an identity-only lookup (no DB write).

    ``found`` is false when neither backend matched the query. In that
    case the other fields are empty / null but still present so the
    frontend can render a single shape.
    """

    found = serializers.BooleanField()
    identity_source = serializers.CharField(allow_blank=True)
    username = serializers.CharField(allow_blank=True)
    first_name = serializers.CharField(allow_blank=True)
    last_name = serializers.CharField(allow_blank=True)
    email = serializers.EmailField(allow_blank=True)
    membership_status = serializers.CharField(allow_blank=True)
    expires_at = serializers.DateTimeField(allow_null=True)
    days_remaining = serializers.IntegerField(allow_null=True)


class PreConvertRequestSerializer(serializers.Serializer):
    query = serializers.CharField(max_length=64)
    notes = serializers.CharField(max_length=2000, allow_blank=True, required=False, default="")


# ---------------------------------------------------------------------------
# Conversion (PR 3)
# ---------------------------------------------------------------------------


class ConvertRequestSerializer(serializers.Serializer):
    """Identify the pre-conversion row to convert.

    Accept either ``id`` (the queue UI calls per-row), or
    ``assigned_username`` (scantty / batch flows). Exactly one is
    required.
    """

    id = serializers.IntegerField(required=False)
    assigned_username = serializers.CharField(max_length=64, required=False, allow_blank=False)

    def validate(self, attrs):
        if not attrs.get("id") and not attrs.get("assigned_username"):
            raise serializers.ValidationError("Either ``id`` or ``assigned_username`` is required.")
        return attrs
