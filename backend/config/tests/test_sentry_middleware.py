"""Tests for `config.middleware.SentryUserScopeMiddleware`.

The middleware is what gives every Sentry event a `username` and the
`oms.user_role` tag. Without these, the Sentry "users affected" count
defaults to anonymous and uid0 can't slice incidents by role.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import RequestFactory

import pytest

from config.middleware import SentryUserScopeMiddleware, _role_for
from membership.models import User

pytestmark = pytest.mark.django_db


class TestRoleFor:
    """Pin the role mapping. Adding finer-grained roles (SIG admin /
    board member) needs a separate tag, not a wider mapping here."""

    def test_unauthenticated_is_anonymous(self):
        # AnonymousUser has is_authenticated=False.
        anon = MagicMock(is_authenticated=False, is_staff=False, is_superuser=False)
        assert _role_for(anon) == "anonymous"

    def test_member_is_member(self):
        user = MagicMock(is_authenticated=True, is_staff=False, is_superuser=False)
        assert _role_for(user) == "member"

    def test_staff_is_staff(self):
        user = MagicMock(is_authenticated=True, is_staff=True, is_superuser=False)
        assert _role_for(user) == "staff"

    def test_superuser_is_admin_even_if_not_staff(self):
        # A superuser without is_staff set is unusual but valid; admin
        # wins so the tag still surfaces the elevated risk.
        user = MagicMock(is_authenticated=True, is_staff=False, is_superuser=True)
        assert _role_for(user) == "admin"

    def test_none_is_anonymous(self):
        # Defensive: middleware can be invoked before request.user is
        # populated (no auth middleware installed). Don't crash.
        assert _role_for(None) == "anonymous"


class TestSentryUserScopeMiddleware:
    """The middleware must always tag the role and only set the user
    payload for authenticated requests (`set_user(None)` for anon would
    clear a stale scope from an earlier request, but Sentry SDK 2.x
    auto-scopes per-request, so a no-op is safer than a wipe)."""

    def _build(self, response_marker="ok"):
        get_response = MagicMock(return_value=response_marker)
        return SentryUserScopeMiddleware(get_response), get_response

    @patch("config.middleware.sentry_sdk")
    def test_anonymous_request_tags_role_but_skips_set_user(self, mock_sentry):
        middleware, get_response = self._build()
        request = RequestFactory().get("/api/health/livez/")
        # The auth middleware would normally attach AnonymousUser; mimic.
        request.user = MagicMock(is_authenticated=False)
        result = middleware(request)
        assert result == "ok"
        mock_sentry.set_tag.assert_called_once_with("oms.user_role", "anonymous")
        mock_sentry.set_user.assert_not_called()
        get_response.assert_called_once_with(request)

    @patch("config.middleware.sentry_sdk")
    def test_authenticated_request_sets_user_and_role(self, mock_sentry):
        user = User.objects.create_user(
            username="member-1",
            email="member-1@test.com",
            password="sentry-mw-pw-12345",
        )
        middleware, _ = self._build()
        request = RequestFactory().get("/api/inventory/items/")
        request.user = user
        middleware(request)
        mock_sentry.set_tag.assert_called_once_with("oms.user_role", "member")
        mock_sentry.set_user.assert_called_once_with(
            {
                "id": user.id,
                "username": user.username,
                "ip_address": "{{auto}}",
            }
        )

    @patch("config.middleware.sentry_sdk")
    def test_staff_request_tags_staff_role(self, mock_sentry):
        user = User.objects.create_user(
            username="staff-1",
            email="staff-1@test.com",
            password="sentry-mw-pw-12345",
            is_staff=True,
        )
        middleware, _ = self._build()
        request = RequestFactory().get("/api/dashboard/")
        request.user = user
        middleware(request)
        mock_sentry.set_tag.assert_called_once_with("oms.user_role", "staff")
