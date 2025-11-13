"""
Rate limiting utilities for QR code generation.

Provides rate limiting based on user authentication status and item ID.
"""

from datetime import timedelta
from typing import Optional

from django.contrib.auth import get_user_model
from django.core.cache import cache

User = get_user_model()


class QRCodeRateLimiter:
    """Rate limiter for QR code generation requests."""

    # Rate limits
    UNAUTHENTICATED_LIMIT = 5  # per day
    AUTHENTICATED_LIMIT = 5  # per minute
    UNAUTHENTICATED_WINDOW = timedelta(days=1)
    AUTHENTICATED_WINDOW = timedelta(minutes=1)

    @staticmethod
    def _get_cache_key(user: Optional[User], item_id: str, item_type: str) -> str:
        """
        Generate a cache key for rate limiting.

        Args:
            user: User instance or None for anonymous users
            item_id: ID of the item (asset, inventory item, or location)
            item_type: Type of item ('asset', 'item', or 'location')

        Returns:
            Cache key string
        """
        if user:
            return f"qr_rate_limit:{item_type}:{item_id}:user:{user.id}"
        else:
            # For anonymous users, use IP address if available
            # For now, we'll use a session-based approach
            return f"qr_rate_limit:{item_type}:{item_id}:anonymous"

    @staticmethod
    def _get_ip_cache_key(ip_address: str, item_id: str, item_type: str) -> str:
        """
        Generate a cache key for IP-based rate limiting.

        Args:
            ip_address: IP address of the request
            item_id: ID of the item
            item_type: Type of item

        Returns:
            Cache key string
        """
        return f"qr_rate_limit:{item_type}:{item_id}:ip:{ip_address}"

    @classmethod
    def check_rate_limit(
        cls,
        user: Optional[User],
        item_id: str,
        item_type: str,
        ip_address: Optional[str] = None,
    ) -> tuple[bool, Optional[str]]:
        """
        Check if a request is within rate limits.

        Args:
            user: User instance or None for anonymous users
            item_id: ID of the item
            item_type: Type of item ('asset', 'item', or 'location')
            ip_address: IP address of the request (for anonymous users)

        Returns:
            Tuple of (is_allowed, error_message)
            - is_allowed: True if request is allowed, False otherwise
            - error_message: Error message if rate limit exceeded, None otherwise
        """
        # Staff and admins have no rate limit
        if user and (user.is_staff or user.is_superuser):
            return True, None

        # Determine rate limit based on authentication status
        if user:
            # Authenticated user
            limit = cls.AUTHENTICATED_LIMIT
            window = cls.AUTHENTICATED_WINDOW
            cache_key = cls._get_cache_key(user, item_id, item_type)
        else:
            # Unauthenticated user
            limit = cls.UNAUTHENTICATED_LIMIT
            window = cls.UNAUTHENTICATED_WINDOW
            # Use IP address if available, otherwise use a generic key
            if ip_address:
                cache_key = cls._get_ip_cache_key(ip_address, item_id, item_type)
            else:
                cache_key = cls._get_cache_key(None, item_id, item_type)

        # Get current count from cache
        current_count = cache.get(cache_key, 0)

        # Check if limit exceeded
        if current_count >= limit:
            if user:
                error_msg = (
                    f"Rate limit exceeded: {limit} requests per "
                    f"{cls.AUTHENTICATED_WINDOW.total_seconds() / 60:.0f} minutes allowed. "
                    f"Please try again later."
                )
            else:
                error_msg = (
                    f"Rate limit exceeded: {limit} requests per day allowed. "
                    f"Please try again later or log in for higher limits."
                )
            return False, error_msg

        # Increment count
        cache.set(cache_key, current_count + 1, timeout=int(window.total_seconds()))

        return True, None

    @classmethod
    def get_remaining_requests(
        cls,
        user: Optional[User],
        item_id: str,
        item_type: str,
        ip_address: Optional[str] = None,
    ) -> int:
        """
        Get the number of remaining requests for a user/item combination.

        Args:
            user: User instance or None for anonymous users
            item_id: ID of the item
            item_type: Type of item
            ip_address: IP address of the request (for anonymous users)

        Returns:
            Number of remaining requests
        """
        # Staff and admins have unlimited requests
        if user and (user.is_staff or user.is_superuser):
            return float("inf")

        # Determine rate limit based on authentication status
        if user:
            limit = cls.AUTHENTICATED_LIMIT
            cache_key = cls._get_cache_key(user, item_id, item_type)
        else:
            limit = cls.UNAUTHENTICATED_LIMIT
            if ip_address:
                cache_key = cls._get_ip_cache_key(ip_address, item_id, item_type)
            else:
                cache_key = cls._get_cache_key(None, item_id, item_type)

        current_count = cache.get(cache_key, 0)
        return max(0, limit - current_count)
