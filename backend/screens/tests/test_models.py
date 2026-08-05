"""
Model tests for the screens app.
"""

from datetime import timedelta

from django.utils import timezone

import pytest

from screens.models import SystemMessage, generate_screen_token
from screens.tests.factories import (
    ScreenContentBlockFactory,
    ScreenFactory,
    ScreenHeartbeatFactory,
    SystemMessageFactory,
)

pytestmark = pytest.mark.django_db


@pytest.mark.unit
class TestScreenModel:
    def test_default_access_token_is_generated(self):
        screen = ScreenFactory()
        assert screen.access_token
        assert len(screen.access_token) >= 32

    def test_tokens_are_unique_per_screen(self):
        a = ScreenFactory()
        b = ScreenFactory()
        assert a.access_token != b.access_token

    def test_generate_screen_token_returns_url_safe_string(self):
        token = generate_screen_token()
        assert isinstance(token, str)
        assert " " not in token

    def test_is_online_false_when_no_heartbeat(self):
        screen = ScreenFactory()
        assert screen.is_online is False
        assert screen.last_heartbeat is None

    def test_is_online_true_with_recent_heartbeat(self):
        screen = ScreenFactory()
        ScreenHeartbeatFactory(screen=screen, reported_at=timezone.now())
        assert screen.is_online is True

    def test_is_online_false_with_stale_heartbeat(self):
        screen = ScreenFactory()
        ScreenHeartbeatFactory(
            screen=screen,
            reported_at=timezone.now() - timedelta(minutes=30),
        )
        assert screen.is_online is False


@pytest.mark.unit
class TestSystemMessageModel:
    def test_is_currently_visible_respects_is_active(self):
        msg = SystemMessageFactory(is_active=False)
        assert msg.is_currently_visible() is False

    def test_is_currently_visible_respects_time_window(self):
        now = timezone.now()
        msg = SystemMessageFactory(
            starts_at=now + timedelta(hours=1),
            ends_at=now + timedelta(hours=2),
        )
        assert msg.is_currently_visible(now) is False
        assert msg.is_currently_visible(now + timedelta(hours=1, minutes=30)) is True

    def test_active_for_now_filters_inactive_and_expired(self):
        now = timezone.now()
        live = SystemMessageFactory()
        SystemMessageFactory(is_active=False)
        SystemMessageFactory(ends_at=now - timedelta(minutes=1))
        SystemMessageFactory(starts_at=now + timedelta(hours=1))
        ids = list(SystemMessage.active_for_now().values_list("id", flat=True))
        assert live.id in ids
        assert len(ids) == 1


@pytest.mark.unit
class TestScreenContentBlockModel:
    def test_blocks_default_enabled_and_ordered(self):
        screen = ScreenFactory()
        a = ScreenContentBlockFactory(screen=screen, order=1)
        b = ScreenContentBlockFactory(screen=screen, order=0)
        ordered = list(screen.content_blocks.order_by("order").values_list("id", flat=True))
        assert ordered == [b.id, a.id]
