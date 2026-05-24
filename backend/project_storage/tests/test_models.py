from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

import pytest

from project_storage.models import (
    DEFAULT_PURGATORY_GRACE_DAYS,
    DEFAULT_REENTRY_COOLDOWN_DAYS,
    DEFAULT_STINT_DAYS,
    ProjectStorageStint,
)
from project_storage.tests.factories import ProjectStorageStintFactory

pytestmark = pytest.mark.django_db


class TestStintStatus:
    def test_active_when_well_inside_window(self):
        stint = ProjectStorageStintFactory(
            started_at=timezone.now() - timedelta(days=5),
        )
        assert stint.compute_status() == ProjectStorageStint.STATUS_ACTIVE

    def test_expiring_soon_within_3_days_of_expiry(self):
        now = timezone.now()
        stint = ProjectStorageStintFactory(
            started_at=now - timedelta(days=DEFAULT_STINT_DAYS - 2),
            expires_at=now + timedelta(days=2),
        )
        assert stint.compute_status() == ProjectStorageStint.STATUS_EXPIRING_SOON

    def test_expired_after_window(self):
        now = timezone.now()
        stint = ProjectStorageStintFactory(
            started_at=now - timedelta(days=DEFAULT_STINT_DAYS + 5),
            expires_at=now - timedelta(days=5),
        )
        assert stint.compute_status() == ProjectStorageStint.STATUS_EXPIRED

    def test_purgatory_warned_after_notice(self):
        now = timezone.now()
        stint = ProjectStorageStintFactory(
            started_at=now - timedelta(days=40),
            expires_at=now - timedelta(days=10),
            notice_sent_at=now - timedelta(days=1),
        )
        assert stint.compute_status() == ProjectStorageStint.STATUS_PURGATORY_WARNED

    def test_purgatory_after_explicit_move(self):
        now = timezone.now()
        stint = ProjectStorageStintFactory(
            started_at=now - timedelta(days=50),
            expires_at=now - timedelta(days=20),
            notice_sent_at=now - timedelta(days=10),
            moved_to_purgatory_at=now - timedelta(days=2),
        )
        assert stint.compute_status() == ProjectStorageStint.STATUS_PURGATORY

    def test_removed_status_wins_over_everything(self):
        stint = ProjectStorageStintFactory(
            notice_sent_at=timezone.now(),
            removed_at=timezone.now(),
        )
        assert stint.compute_status() == ProjectStorageStint.STATUS_REMOVED


class TestStintHelpers:
    def test_save_backfills_expires_at_from_started_at(self):
        started = timezone.now()
        stint = ProjectStorageStint(username="u", started_at=started)
        stint.save()
        delta = stint.expires_at - started
        assert delta == timedelta(days=DEFAULT_STINT_DAYS)

    def test_purgatory_at_is_notice_plus_grace(self):
        notice = timezone.now() - timedelta(days=2)
        stint = ProjectStorageStintFactory(notice_sent_at=notice)
        assert stint.purgatory_at == notice + timedelta(days=DEFAULT_PURGATORY_GRACE_DAYS)

    def test_purgatory_at_is_none_without_notice(self):
        stint = ProjectStorageStintFactory()
        assert stint.purgatory_at is None

    def test_display_name_prefers_first_last(self):
        stint = ProjectStorageStintFactory(first_name="Alex", last_name="Smith", username="asmith")
        assert stint.display_name == "Alex Smith"

    def test_display_name_falls_back_to_username(self):
        stint = ProjectStorageStintFactory(first_name="", last_name="", username="asmith")
        assert stint.display_name == "asmith"

    def test_expiry_week_and_day_returns_iso_week_and_doy(self):
        # Fixed date: 2026-12-31 is week 53, day 365.
        stint = ProjectStorageStintFactory(
            expires_at=timezone.make_aware(timezone.datetime(2026, 12, 31, 12, 0)),
        )
        week, doy = stint.expiry_week_and_day
        assert isinstance(week, int) and 1 <= week <= 53
        assert isinstance(doy, int) and 1 <= doy <= 366


class TestCooldown:
    def test_no_prior_stint_means_no_cooldown(self):
        assert ProjectStorageStint.cooldown_blocks_new_stint("new-user") is None

    def test_recent_removal_blocks_new_stint(self):
        username = "blocky"
        ProjectStorageStintFactory(
            username=username,
            removed_at=timezone.now() - timedelta(days=1),
        )
        unblock = ProjectStorageStint.cooldown_blocks_new_stint(username)
        assert unblock is not None
        assert unblock > timezone.now()

    def test_old_removal_does_not_block(self):
        username = "freeagent"
        ProjectStorageStintFactory(
            username=username,
            removed_at=timezone.now() - timedelta(days=DEFAULT_REENTRY_COOLDOWN_DAYS + 1),
        )
        assert ProjectStorageStint.cooldown_blocks_new_stint(username) is None

    def test_unremoved_stint_does_not_count_toward_cooldown(self):
        # Cooldown is *only* about the post-removal break, not about active
        # stints — the active-stint check handles that case.
        username = "openrun"
        ProjectStorageStintFactory(username=username, removed_at=None)
        assert ProjectStorageStint.cooldown_blocks_new_stint(username) is None


class TestActiveStint:
    def test_recognises_active_stint(self):
        ProjectStorageStintFactory(username="abc")
        assert ProjectStorageStint.member_has_active_stint("abc") is True

    def test_removed_stint_is_not_active(self):
        ProjectStorageStintFactory(username="abc", removed_at=timezone.now())
        assert ProjectStorageStint.member_has_active_stint("abc") is False

    def test_purgatoried_stint_is_not_active(self):
        ProjectStorageStintFactory(
            username="abc",
            notice_sent_at=timezone.now(),
            moved_to_purgatory_at=timezone.now(),
        )
        assert ProjectStorageStint.member_has_active_stint("abc") is False

    def test_expired_but_unremoved_still_counts_as_active(self):
        # Until the warden resolves it, the slot is still occupied.
        ProjectStorageStintFactory(
            username="abc",
            expires_at=timezone.now() - timedelta(days=5),
        )
        assert ProjectStorageStint.member_has_active_stint("abc") is True
