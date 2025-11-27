"""
Custom JWT token classes with user-based expiration times.
"""

from datetime import timedelta

from rest_framework_simplejwt.tokens import AccessToken, RefreshToken


class CustomAccessToken(AccessToken):
    """
    Custom access token with user-based expiration.
    """

    pass  # Expiration will be set dynamically


class CustomRefreshToken(RefreshToken):
    """
    Custom refresh token that sets different expiration times based on user type.
    - Superusers: 20 minutes of inactivity
    - Regular users: 7 days of inactivity
    """

    access_token_class = CustomAccessToken

    def __init__(self, token=None, verify=True, user=None):
        """
        Initialize token with user-specific expiration.
        """
        super().__init__(token, verify)
        self._user = user
        if user and not token:
            # Set refresh token lifetime based on user type
            if user.is_superuser:
                # Superusers: 1 hour refresh token (allows refresh within 1 hour)
                self.lifetime = timedelta(hours=1)
            else:
                # Regular users: 30 days refresh token
                self.lifetime = timedelta(days=30)

    @property
    def access_token(self):
        """
        Override access_token property to set lifetime based on user type.
        """
        access = self.access_token_class()
        access["user_id"] = self["user_id"]
        access["username"] = self.get("username", "")
        access["is_superuser"] = self.get("is_superuser", False)
        access["is_staff"] = self.get("is_staff", False)

        # Set access token lifetime based on user type
        if self.get("is_superuser", False):
            # Superusers: 20 minutes
            access.lifetime = timedelta(minutes=20)
        else:
            # Regular users: 7 days
            access.lifetime = timedelta(days=7)

        return access

    @classmethod
    def for_user(cls, user):
        """
        Generate tokens for a user with expiration based on user type.
        """
        token = cls(user=user)
        token["user_id"] = user.pk
        token["username"] = user.username
        token["is_superuser"] = user.is_superuser
        token["is_staff"] = user.is_staff

        return token
