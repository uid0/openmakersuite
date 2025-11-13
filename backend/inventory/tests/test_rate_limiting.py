"""
Tests for QR code rate limiting.
"""

from django.core.cache import cache

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from inventory.tests.factories import AssetFactory, InventoryItemFactory, LocationFactory
from inventory.utils.rate_limiting import QRCodeRateLimiter


@pytest.mark.unit
class TestQRCodeRateLimiter:
    """Tests for QR code rate limiting."""

    def setup_method(self):
        """Clear cache before each test."""
        cache.clear()

    def test_unauthenticated_user_rate_limit(self):
        """Test rate limiting for unauthenticated users."""
        item_id = "test-item-123"
        item_type = "item"

        # First 5 requests should be allowed
        for i in range(5):
            is_allowed, error_msg = QRCodeRateLimiter.check_rate_limit(
                user=None, item_id=item_id, item_type=item_type, ip_address="127.0.0.1"
            )
            assert is_allowed, f"Request {i+1} should be allowed"
            assert error_msg is None

        # 6th request should be denied
        is_allowed, error_msg = QRCodeRateLimiter.check_rate_limit(
            user=None, item_id=item_id, item_type=item_type, ip_address="127.0.0.1"
        )
        assert not is_allowed
        assert "Rate limit exceeded" in error_msg
        assert "per day" in error_msg

    def test_authenticated_user_rate_limit(self, authenticated_client):
        """Test rate limiting for authenticated users."""
        client, user = authenticated_client
        item_id = "test-item-123"
        item_type = "item"

        # First 5 requests should be allowed
        for i in range(5):
            is_allowed, error_msg = QRCodeRateLimiter.check_rate_limit(
                user=user, item_id=item_id, item_type=item_type, ip_address="127.0.0.1"
            )
            assert is_allowed, f"Request {i+1} should be allowed"
            assert error_msg is None

        # 6th request should be denied
        is_allowed, error_msg = QRCodeRateLimiter.check_rate_limit(
            user=user, item_id=item_id, item_type=item_type, ip_address="127.0.0.1"
        )
        assert not is_allowed
        assert "Rate limit exceeded" in error_msg
        assert "per minute" in error_msg

    def test_staff_user_no_rate_limit(self, admin_user):
        """Test that staff users have no rate limit."""
        item_id = "test-item-123"
        item_type = "item"

        # Staff users should have unlimited requests
        for i in range(20):
            is_allowed, error_msg = QRCodeRateLimiter.check_rate_limit(
                user=admin_user, item_id=item_id, item_type=item_type, ip_address="127.0.0.1"
            )
            assert is_allowed, f"Request {i+1} should be allowed for staff"
            assert error_msg is None

    def test_different_items_separate_limits(self):
        """Test that rate limits are per item."""
        item_id_1 = "test-item-1"
        item_id_2 = "test-item-2"
        item_type = "item"

        # Exhaust limit for item 1
        for _ in range(5):
            QRCodeRateLimiter.check_rate_limit(
                user=None, item_id=item_id_1, item_type=item_type, ip_address="127.0.0.1"
            )

        # Item 2 should still have full limit
        is_allowed, error_msg = QRCodeRateLimiter.check_rate_limit(
            user=None, item_id=item_id_2, item_type=item_type, ip_address="127.0.0.1"
        )
        assert is_allowed
        assert error_msg is None

    def test_get_remaining_requests(self, authenticated_client):
        """Test getting remaining requests."""
        client, user = authenticated_client
        item_id = "test-item-123"
        item_type = "item"

        # Initially should have 5 remaining
        remaining = QRCodeRateLimiter.get_remaining_requests(
            user=user, item_id=item_id, item_type=item_type, ip_address="127.0.0.1"
        )
        assert remaining == 5

        # Make 2 requests
        QRCodeRateLimiter.check_rate_limit(
            user=user, item_id=item_id, item_type=item_type, ip_address="127.0.0.1"
        )
        QRCodeRateLimiter.check_rate_limit(
            user=user, item_id=item_id, item_type=item_type, ip_address="127.0.0.1"
        )

        # Should have 3 remaining
        remaining = QRCodeRateLimiter.get_remaining_requests(
            user=user, item_id=item_id, item_type=item_type, ip_address="127.0.0.1"
        )
        assert remaining == 3

    def test_staff_unlimited_remaining(self, admin_user):
        """Test that staff users show unlimited remaining requests."""
        item_id = "test-item-123"
        item_type = "item"

        remaining = QRCodeRateLimiter.get_remaining_requests(
            user=admin_user, item_id=item_id, item_type=item_type, ip_address="127.0.0.1"
        )
        assert remaining == float("inf")


@pytest.mark.django_db
class TestQRCodeRateLimitingAPI:
    """Integration tests for QR code rate limiting in API endpoints."""

    def setup_method(self):
        """Set up test client and clear cache."""
        self.client = APIClient()
        cache.clear()

    def test_asset_generate_qr_rate_limit_unauthenticated(self):
        """Test rate limiting for unauthenticated asset QR generation."""
        asset = AssetFactory()

        # First 5 requests should succeed
        for _ in range(5):
            response = self.client.post(f"/api/inventory/assets/{asset.id}/generate_qr/")
            assert response.status_code in [
                status.HTTP_200_OK,
                status.HTTP_201_CREATED,
                status.HTTP_500_INTERNAL_SERVER_ERROR,  # May fail for other reasons
            ], "Request should not be rate limited"

        # 6th request should be rate limited
        response = self.client.post(f"/api/inventory/assets/{asset.id}/generate_qr/")
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert "Rate limit exceeded" in str(response.data)

    def test_asset_generate_qr_no_rate_limit_staff(self, admin_user):
        """Test that staff users have no rate limit."""
        self.client.force_authenticate(user=admin_user)
        asset = AssetFactory()

        # Staff should be able to make many requests
        for _ in range(10):
            response = self.client.post(f"/api/inventory/assets/{asset.id}/generate_qr/")
            assert response.status_code != status.HTTP_429_TOO_MANY_REQUESTS

    def test_item_generate_qr_rate_limit_authenticated(self, authenticated_client):
        """Test rate limiting for authenticated item QR generation."""
        client, user = authenticated_client
        item = InventoryItemFactory()

        # First 5 requests should succeed
        for _ in range(5):
            response = client.post(f"/api/inventory/items/{item.id}/generate_qr/")
            assert response.status_code != status.HTTP_429_TOO_MANY_REQUESTS

        # 6th request should be rate limited
        response = client.post(f"/api/inventory/items/{item.id}/generate_qr/")
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert "Rate limit exceeded" in str(response.data)

    def test_location_generate_qr_rate_limit(self):
        """Test rate limiting for location QR generation."""
        location = LocationFactory()

        # First 5 requests should succeed
        for _ in range(5):
            response = self.client.post(f"/api/inventory/locations/{location.id}/generate_qr/")
            assert response.status_code != status.HTTP_429_TOO_MANY_REQUESTS

        # 6th request should be rate limited
        response = self.client.post(f"/api/inventory/locations/{location.id}/generate_qr/")
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert "Rate limit exceeded" in str(response.data)
