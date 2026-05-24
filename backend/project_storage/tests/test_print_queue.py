from __future__ import annotations

from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from project_storage.models import ProjectStorageEvent, ProjectStorageStint
from project_storage.tests.factories import ProjectStorageStintFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return APIClient()


class TestPrintQueue:
    def test_lists_unprinted_active_stints(self, client):
        # Eligible: printed_at is null, not removed, not purgatoried.
        eligible = ProjectStorageStintFactory(print_target="brother_ql")
        # Excluded: already printed.
        ProjectStorageStintFactory(printed_at=timezone.now())
        # Excluded: removed.
        ProjectStorageStintFactory(removed_at=timezone.now())
        # Excluded: purgatoried.
        ProjectStorageStintFactory(
            notice_sent_at=timezone.now(),
            moved_to_purgatory_at=timezone.now(),
        )

        resp = client.get("/api/project-storage/stints/print-queue/")
        assert resp.status_code == 200
        body = resp.json()
        ids = [row["stint_id"] for row in body]
        assert ids == [eligible.stint_id]
        # The daemon needs the absolute label URL.
        assert body[0]["label_url"].endswith(
            f"/project-storage/stints/{eligible.stint_id}/label/?printer=brother_ql"
        )

    def test_label_url_honours_print_target(self, client):
        ProjectStorageStintFactory(print_target="epson_tm", username="e")
        resp = client.get("/api/project-storage/stints/print-queue/")
        body = resp.json()
        assert any("printer=epson_tm" in row["label_url"] for row in body)

    def test_defaults_to_brother_ql_when_target_blank(self, client):
        ProjectStorageStintFactory(print_target="", username="empty")
        resp = client.get("/api/project-storage/stints/print-queue/")
        body = resp.json()
        # blank print_target -> brother_ql default
        target_for_empty = next(r for r in body if r["print_target"] == "brother_ql")
        assert "printer=brother_ql" in target_for_empty["label_url"]

    def test_returns_in_created_at_order_oldest_first(self, client):
        first = ProjectStorageStintFactory(username="first")
        second = ProjectStorageStintFactory(username="second")
        resp = client.get("/api/project-storage/stints/print-queue/")
        body = resp.json()
        ids = [row["stint_id"] for row in body]
        assert ids.index(first.stint_id) < ids.index(second.stint_id)


class TestMarkPrinted:
    def test_sets_printed_at_and_records_event(self, client):
        stint = ProjectStorageStintFactory()
        resp = client.post(
            f"/api/project-storage/stints/{stint.stint_id}/mark-printed/",
            {"note": "from test"},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        stint.refresh_from_db()
        assert stint.printed_at is not None
        assert stint.events.filter(actor_label="pi-daemon: printed").exists()

    def test_idempotent_no_duplicate_event(self, client):
        already = timezone.now()
        stint = ProjectStorageStintFactory(printed_at=already)
        resp = client.post(f"/api/project-storage/stints/{stint.stint_id}/mark-printed/")
        assert resp.status_code == 200
        stint.refresh_from_db()
        # printed_at unchanged + no new event recorded
        assert stint.printed_at == already
        assert stint.events.filter(actor_label="pi-daemon: printed").count() == 0

    def test_drops_off_queue_after_mark_printed(self, client):
        stint = ProjectStorageStintFactory()
        assert any(
            row["stint_id"] == stint.stint_id
            for row in client.get("/api/project-storage/stints/print-queue/").json()
        )
        client.post(f"/api/project-storage/stints/{stint.stint_id}/mark-printed/")
        assert not any(
            row["stint_id"] == stint.stint_id
            for row in client.get("/api/project-storage/stints/print-queue/").json()
        )
