"""
Custom JWT token classes with user-based expiration times.

In addition to per-user-type lifetimes, every token validated through these
classes is checked against ``User.tokens_valid_after`` (notifications FP3,
oms-ltqs3). The "this wasn't me" / revoke-all action advances that timestamp,
and any access *or* refresh token whose ``iat`` (issued-at) predates it is
rejected here. This is the single chokepoint every JWT auth flow passes
through — ``SIMPLE_JWT["AUTH_TOKEN_CLASSES"]`` points at
:class:`CustomAccessToken`, and the refresh endpoint instantiates
:class:`CustomRefreshToken` — so a revoke forces a full re-auth across the REST
API, the DRF browsable API, and the admin.

We deliberately enforce revocation here rather than via the simple-JWT
blacklist app: that app only invalidates *refresh* tokens (leaving stateless
access tokens valid for up to their 7-day lifetime), and our custom
``for_user`` below never records ``OutstandingToken`` rows for it to blacklist.
The ``tokens_valid_after`` cutoff invalidates both token types at once.
"""

from datetime import timedelta

from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken
from rest_framework_simplejwt.utils import datetime_from_epoch


def _reject_if_revoked(token):
    """Raise :class:`TokenError` when ``token`` predates its owner's revoke.

    No-op when the user has never revoked (``tokens_valid_after`` is null) or
    the token is missing the claims we need. The lookup is a single indexed
    primary-key read; it runs on every token validation (i.e. every
    authenticated request), an acceptable cost for making revoke-all effective
    against otherwise-stateless access tokens.
    """
    payload = getattr(token, "payload", None) or {}
    user_id = payload.get("user_id")
    issued_at = payload.get("iat")
    if user_id is None or issued_at is None:
        return

    # Imported lazily: this module is imported very early via settings
    # (AUTH_TOKEN_CLASSES), before the app registry is necessarily ready.
    from django.contrib.auth import get_user_model

    cutoff = (
        get_user_model()
        .objects.filter(pk=user_id)
        .values_list("tokens_valid_after", flat=True)
        .first()
    )
    # `iat` is whole-second epoch; comparing strictly (`<`) means a token minted
    # in the same second as the revoke is invalidated too — the safe direction.
    if cutoff is not None and datetime_from_epoch(issued_at) < cutoff:
        raise TokenError("Token has been revoked (issued before tokens_valid_after).")


class CustomAccessToken(AccessToken):
    """
    Custom access token with user-based expiration.
    """

    def verify(self, *args, **kwargs):
        super().verify(*args, **kwargs)
        _reject_if_revoked(self)


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

    def verify(self, *args, **kwargs):
        super().verify(*args, **kwargs)
        _reject_if_revoked(self)

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
