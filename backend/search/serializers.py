"""
Serializers for search functionality.
"""

from rest_framework import serializers

from .models import RecentSearch


class SearchResultSerializer(serializers.Serializer):
    """Serializer for search results from global search."""

    id = serializers.CharField()
    type = serializers.CharField()
    title = serializers.CharField()
    subtitle = serializers.CharField(required=False, allow_null=True)
    url = serializers.CharField()


class RecentSearchSerializer(serializers.ModelSerializer):
    """Serializer for recent searches."""

    class Meta:
        model = RecentSearch
        fields = ["id", "query", "result_type", "result_id", "result_title", "searched_at"]
        read_only_fields = ["searched_at"]


class RecentSearchCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating recent searches."""

    class Meta:
        model = RecentSearch
        fields = ["query", "result_type", "result_id", "result_title"]
