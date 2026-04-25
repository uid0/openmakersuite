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
