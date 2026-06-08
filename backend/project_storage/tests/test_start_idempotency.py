"""Tests for the gh-714 idempotency on POST /project-storage/stints/start/.

A second submit with the same payload inside the dedup window resolves
to the EXISTING stint with HTTP 200, not a duplicate row and not a 409.
After the window, the second submit either creates a new stint or fires
the existing 409 active_stint_exists path — both are the correct shapes
for distinct user intents.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from project_storage.models import ProjectStorageStint
from project_storage.tests.factories import ProjectStorageStintFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return APIClient()


def _payload(**overrides):
    base = {
        "username": "newbie",
        "first_name": "New",
        "last_name": "Bie",
        "email": "newbie@example.com",
        "project_title": "Restore Schwinn",
        "storage_location_name": "Shelf A",
    }
    base.update(overrides)
    return base


class TestStartIdempotency:
    def test_duplicate_in_window_returns_existing_with_200(self, client):
        first = client.post("/api/project-storage/stints/start/", _payload(), format="json")
        assert first.status_code == 201, first.content

        second = client.post("/api/project-storage/stints/start/", _payload(), format="json")
        # Idempotent: returns the existing stint, not 409 active_stint_exists.
        assert second.status_code == 200, second.content
        assert second.json()["stint_id"] == first.json()["stint_id"]

        # And no duplicate row was created.
        assert ProjectStorageStint.objects.filter(username="newbie").count() == 1

    def test_different_project_title_is_not_a_duplicate(self, client):
        first = client.post(
            "/api/project-storage/stints/start/",
            _payload(project_title="Restore Schwinn"),
            format="json",
        )
        assert first.status_code == 201

        # Different project_title — same member but a different stint
        # intent. The standard 409 active_stint_exists path fires
        # because the member already has an unresolved stint; we are
        # NOT silently treating this as a duplicate.
        second = client.post(
            "/api/project-storage/stints/start/",
            _payload(project_title="Different Project"),
            format="json",
        )
        assert second.status_code == 409
        assert second.json()["code"] == "active_stint_exists"

    def test_different_username_is_not_a_duplicate(self, client):
        first = client.post(
            "/api/project-storage/stints/start/",
            _payload(username="ada"),
            format="json",
        )
        assert first.status_code == 201

        second = client.post(
            "/api/project-storage/stints/start/",
            _payload(username="bob"),
            format="json",
        )
        # Different member entirely — distinct row.
        assert second.status_code == 201
        assert second.json()["stint_id"] != first.json()["stint_id"]
        assert ProjectStorageStint.objects.count() == 2

    def test_stint_older_than_window_falls_back_to_409(self, client):
        # An older stint (outside the dedup window, but still unresolved)
        # is the legitimate "you already have an active stint" case —
        # the member should be told to go talk to the warden, not
        # silently get a 200 with the old stint.
        ProjectStorageStintFactory(
            username="busy",
            started_at=timezone.now() - timedelta(hours=2),
            project_title="Old Project",
            storage_location_name="Shelf A",
        )
        resp = client.post(
            "/api/project-storage/stints/start/",
            _payload(username="busy", project_title="Different Now"),
            format="json",
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "active_stint_exists"

    def test_same_payload_after_window_with_no_active_stint_creates_new(self, client):
        # Member's prior stint is closed (removed) more than the cooldown
        # ago, so a new submit should land cleanly. Just verifies that
        # the dedup helper doesn't accidentally match against removed
        # stints — those should be invisible to it.
        from project_storage.models import DEFAULT_REENTRY_COOLDOWN_DAYS

        old = ProjectStorageStintFactory(
            username="clear",
            project_title="Restore Schwinn",
            storage_location_name="Shelf A",
            removed_at=timezone.now() - timedelta(days=DEFAULT_REENTRY_COOLDOWN_DAYS + 1),
        )

        resp = client.post(
            "/api/project-storage/stints/start/",
            _payload(username="clear"),
            format="json",
        )
        assert resp.status_code == 201, resp.content
        # Distinct stint ID from the old (removed) one.
        assert resp.json()["stint_id"] != old.stint_id
