"""Tests for the gh-714 ``find_recent_duplicate`` helper."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

import pytest

from config.idempotency import DEFAULT_IDEMPOTENCY_WINDOW, find_recent_duplicate
from project_storage.models import ProjectStorageStint
from project_storage.tests.factories import ProjectStorageStintFactory

pytestmark = pytest.mark.django_db


class TestFindRecentDuplicate:
    def test_matches_within_window(self):
        stint = ProjectStorageStintFactory(username="alice")
        match = find_recent_duplicate(
            ProjectStorageStint,
            lookup_fields={"username": "alice"},
            created_at_field="started_at",
        )
        assert match is not None
        assert match.id == stint.id

    def test_misses_outside_window(self):
        ProjectStorageStintFactory(username="alice", started_at=timezone.now() - timedelta(hours=1))
        match = find_recent_duplicate(
            ProjectStorageStint,
            lookup_fields={"username": "alice"},
            created_at_field="started_at",
        )
        # Default 5-minute window — 1 hour ago is outside.
        assert match is None

    def test_returns_most_recent_when_multiple_match(self):
        ProjectStorageStintFactory(
            username="alice", started_at=timezone.now() - timedelta(minutes=2)
        )
        newer = ProjectStorageStintFactory(
            username="alice", started_at=timezone.now() - timedelta(seconds=10)
        )
        match = find_recent_duplicate(
            ProjectStorageStint,
            lookup_fields={"username": "alice"},
            created_at_field="started_at",
        )
        assert match.id == newer.id

    def test_explicit_window_widens(self):
        ProjectStorageStintFactory(
            username="alice", started_at=timezone.now() - timedelta(minutes=30)
        )
        # Default 5-minute window misses.
        assert (
            find_recent_duplicate(
                ProjectStorageStint,
                lookup_fields={"username": "alice"},
                created_at_field="started_at",
            )
            is None
        )
        # Explicit 1-hour window catches it.
        match = find_recent_duplicate(
            ProjectStorageStint,
            lookup_fields={"username": "alice"},
            created_at_field="started_at",
            window=timedelta(hours=1),
        )
        assert match is not None

    def test_lookup_fields_filter_correctly(self):
        ProjectStorageStintFactory(username="alice", project_title="A")
        # Same window, different project_title — no match.
        match = find_recent_duplicate(
            ProjectStorageStint,
            lookup_fields={"username": "alice", "project_title": "B"},
            created_at_field="started_at",
        )
        assert match is None

    def test_default_window_constant(self):
        # Document the default so a behavior change is visible.
        assert DEFAULT_IDEMPOTENCY_WINDOW == timedelta(minutes=5)
