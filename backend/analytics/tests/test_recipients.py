"""Tests for ``analytics.services.recipients``."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

import pytest

from analytics.services.recipients import (
    ANALYTICS_GROUP_NAME,
    get_analytics_group,
    resolve_pulse_recipients,
)

User = get_user_model()
pytestmark = pytest.mark.django_db


def _user(username, email, **flags):
    return User.objects.create_user(
        username=username,
        email=email,
        password="x" * 24,
        **flags,
    )


class TestResolvePulseRecipients:
    def test_empty_when_nothing_configured(self, settings):
        if hasattr(settings, "BOARD_REPORT_EMAILS"):
            del settings.BOARD_REPORT_EMAILS
        assert resolve_pulse_recipients() == []

    def test_env_only(self, settings):
        settings.BOARD_REPORT_EMAILS = "alice@example.com,bob@example.com"
        assert sorted(resolve_pulse_recipients()) == ["alice@example.com", "bob@example.com"]

    def test_group_only(self, settings):
        settings.BOARD_REPORT_EMAILS = ""
        group = get_analytics_group()
        u = _user("carol", "carol@example.com")
        u.groups.add(group)
        assert resolve_pulse_recipients() == ["carol@example.com"]

    def test_dedupes_case_insensitive(self, settings):
        settings.BOARD_REPORT_EMAILS = "Alice@Example.com"
        group = get_analytics_group()
        u = _user("alice_user", "alice@example.com")
        u.groups.add(group)
        result = resolve_pulse_recipients()
        # Env address is preserved (first seen), group duplicate dropped.
        assert result == ["Alice@Example.com"]

    def test_group_inactive_user_excluded(self, settings):
        settings.BOARD_REPORT_EMAILS = ""
        group = get_analytics_group()
        u = _user("inactive", "inactive@example.com", is_active=False)
        u.groups.add(group)
        assert resolve_pulse_recipients() == []

    def test_group_user_without_email_excluded(self, settings):
        settings.BOARD_REPORT_EMAILS = ""
        group = get_analytics_group()
        u = _user("noemail", "")
        u.groups.add(group)
        assert resolve_pulse_recipients() == []

    def test_invalid_env_address_dropped(self, settings):
        settings.BOARD_REPORT_EMAILS = "not-an-email,real@example.com,also bad"
        assert resolve_pulse_recipients() == ["real@example.com"]

    def test_creates_group_lazily(self, settings):
        settings.BOARD_REPORT_EMAILS = ""
        Group.objects.filter(name=ANALYTICS_GROUP_NAME).delete()
        # Calling resolve creates the group on first read.
        resolve_pulse_recipients()
        assert Group.objects.filter(name=ANALYTICS_GROUP_NAME).exists()
