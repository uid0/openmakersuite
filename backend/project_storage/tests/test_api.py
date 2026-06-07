from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from project_storage.models import (
    DEFAULT_REENTRY_COOLDOWN_DAYS,
    ProjectStorageEvent,
    ProjectStorageStint,
)
from project_storage.tests.factories import ProjectStorageStintFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def staff_client():
    User = get_user_model()
    user = User.objects.create_user(username="warden", password="x", is_staff=True)
    api = APIClient()
    api.force_authenticate(user=user)
    return api


# ---------------------------------------------------------------------------
# Self-service kiosk: POST /api/project-storage/stints/start/
# ---------------------------------------------------------------------------


class TestKioskStart:
    def test_creates_stint_with_30_day_expiry(self, client):
        resp = client.post(
            "/api/project-storage/stints/start/",
            {
                "username": "newbie",
                "first_name": "New",
                "last_name": "Bie",
                "email": "newbie@example.com",
                "project_title": "Restore Schwinn",
                "storage_location_name": "Shelf A",
            },
            format="json",
        )
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["username"] == "newbie"
        assert body["stint_id"].startswith("PS-")
        # 30 day window — allow a few seconds of test-runtime drift.
        expires_at = timezone.datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00"))
        started_at = timezone.datetime.fromisoformat(body["started_at"].replace("Z", "+00:00"))
        delta = expires_at - started_at
        assert timedelta(days=29, hours=23) <= delta <= timedelta(days=30, hours=1)
        # Audit event logged.
        stint = ProjectStorageStint.objects.get(stint_id=body["stint_id"])
        assert stint.events.filter(event_type=ProjectStorageEvent.EVENT_CREATED).exists()

    def test_blocks_member_with_active_stint(self, client):
        ProjectStorageStintFactory(username="busy")
        resp = client.post(
            "/api/project-storage/stints/start/",
            {"username": "busy"},
            format="json",
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "active_stint_exists"

    def test_blocks_member_during_cooldown(self, client):
        ProjectStorageStintFactory(
            username="cooldown",
            removed_at=timezone.now() - timedelta(hours=12),
        )
        resp = client.post(
            "/api/project-storage/stints/start/",
            {"username": "cooldown"},
            format="json",
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "cooldown_active"

    def test_allows_member_after_cooldown_window(self, client):
        ProjectStorageStintFactory(
            username="clear",
            removed_at=timezone.now() - timedelta(days=DEFAULT_REENTRY_COOLDOWN_DAYS + 1),
        )
        resp = client.post(
            "/api/project-storage/stints/start/",
            {"username": "clear"},
            format="json",
        )
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Warden lookups: GET /api/project-storage/stints/<stint_id>/
# and by-member.
# ---------------------------------------------------------------------------


class TestWardenLookups:
    def test_show_requires_staff(self, client):
        stint = ProjectStorageStintFactory()
        resp = client.get(f"/api/project-storage/stints/{stint.stint_id}/")
        assert resp.status_code in (401, 403)

    def test_show_returns_stint_with_status(self, staff_client):
        stint = ProjectStorageStintFactory()
        resp = staff_client.get(f"/api/project-storage/stints/{stint.stint_id}/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["stint_id"] == stint.stint_id
        assert body["status"] == "active"
        assert "expiry_week" in body
        assert "expiry_day_of_year" in body

    def test_by_member_returns_history_most_recent_first(self, staff_client):
        old = ProjectStorageStintFactory(
            username="serial", started_at=timezone.now() - timedelta(days=60)
        )
        # Older one had to be already removed for this to be a coherent timeline.
        old.removed_at = timezone.now() - timedelta(days=30)
        old.save()
        new = ProjectStorageStintFactory(username="serial")
        resp = staff_client.get("/api/project-storage/stints/by-member/serial/")
        assert resp.status_code == 200
        ids = [row["stint_id"] for row in resp.json()]
        assert ids == [new.stint_id, old.stint_id]


# ---------------------------------------------------------------------------
# Warden mutating actions
# ---------------------------------------------------------------------------


class TestSendViolationNotice:
    def test_sends_email_and_records_event(self, staff_client):
        stint = ProjectStorageStintFactory(
            email="member@example.com",
            started_at=timezone.now() - timedelta(days=40),
            expires_at=timezone.now() - timedelta(days=10),
        )
        resp = staff_client.post(
            f"/api/project-storage/stints/{stint.stint_id}/send-violation-notice/"
        )
        assert resp.status_code == 200, resp.content
        stint.refresh_from_db()
        assert stint.notice_sent_at is not None
        assert stint.events.filter(event_type=ProjectStorageEvent.EVENT_NOTICE_SENT).exists()
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["member@example.com"]
        assert stint.stint_id in mail.outbox[0].subject

    def test_rejects_notice_on_active_non_expiring_stint(self, staff_client):
        stint = ProjectStorageStintFactory()  # well-inside window
        resp = staff_client.post(
            f"/api/project-storage/stints/{stint.stint_id}/send-violation-notice/"
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "invalid_state_for_notice"

    def test_member_with_no_email_returns_422(self, staff_client):
        stint = ProjectStorageStintFactory(
            email="",
            started_at=timezone.now() - timedelta(days=40),
            expires_at=timezone.now() - timedelta(days=10),
        )
        resp = staff_client.post(
            f"/api/project-storage/stints/{stint.stint_id}/send-violation-notice/"
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "missing_email"


class TestMoveToPurgatory:
    def test_requires_prior_notice(self, staff_client):
        stint = ProjectStorageStintFactory(
            started_at=timezone.now() - timedelta(days=40),
            expires_at=timezone.now() - timedelta(days=10),
        )
        resp = staff_client.post(
            f"/api/project-storage/stints/{stint.stint_id}/move-to-purgatory/",
            {"purgatory_location_name": "Project Purgatory"},
            format="json",
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "notice_required"

    def test_moves_after_notice(self, staff_client):
        stint = ProjectStorageStintFactory(
            notice_sent_at=timezone.now() - timedelta(days=8),
            expires_at=timezone.now() - timedelta(days=15),
            started_at=timezone.now() - timedelta(days=45),
        )
        resp = staff_client.post(
            f"/api/project-storage/stints/{stint.stint_id}/move-to-purgatory/",
            {"purgatory_location_name": "Project Purgatory"},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        stint.refresh_from_db()
        assert stint.moved_to_purgatory_at is not None
        assert stint.purgatory_location_name == "Project Purgatory"
        assert stint.events.filter(event_type=ProjectStorageEvent.EVENT_MOVED_TO_PURGATORY).exists()


class TestMarkRemoved:
    def test_marks_and_starts_cooldown(self, staff_client):
        stint = ProjectStorageStintFactory()
        resp = staff_client.post(
            f"/api/project-storage/stints/{stint.stint_id}/mark-removed/",
            {"note": "cleared by member at 4pm"},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        stint.refresh_from_db()
        assert stint.removed_at is not None
        assert stint.events.filter(event_type=ProjectStorageEvent.EVENT_REMOVED).exists()
        # Cooldown should now be enforced for this username.
        assert ProjectStorageStint.cooldown_blocks_new_stint(stint.username) is not None

    def test_idempotent_returns_409(self, staff_client):
        stint = ProjectStorageStintFactory(removed_at=timezone.now())
        resp = staff_client.post(f"/api/project-storage/stints/{stint.stint_id}/mark-removed/")
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Label render endpoint — Pi daemon pulls this
# ---------------------------------------------------------------------------


class TestLabelEndpoint:
    def test_default_printer_returns_png(self, client):
        stint = ProjectStorageStintFactory()
        resp = client.get(f"/api/project-storage/stints/{stint.stint_id}/label/")
        assert resp.status_code == 200
        assert resp["Content-Type"] == "image/png"
        assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_epson_printer_returns_png(self, client):
        stint = ProjectStorageStintFactory()
        resp = client.get(f"/api/project-storage/stints/{stint.stint_id}/label/?printer=epson_tm")
        assert resp.status_code == 200
        assert resp["Content-Type"] == "image/png"

    def test_unknown_printer_returns_400(self, client):
        stint = ProjectStorageStintFactory()
        resp = client.get(f"/api/project-storage/stints/{stint.stint_id}/label/?printer=daisychain")
        assert resp.status_code == 400
        assert resp.json()["code"] == "bad_printer"

    def test_unknown_stint_returns_404(self, client):
        resp = client.get("/api/project-storage/stints/PS-NOPE0000/label/")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# List endpoint + status filter + StorageAdmin permission
# ---------------------------------------------------------------------------


@pytest.fixture
def storage_admin_client():
    """Member of the 'Storage Admin' group, NOT is_staff."""
    from django.contrib.auth.models import Group

    User = get_user_model()
    user = User.objects.create_user(username="storageadmin", password="x")
    group, _ = Group.objects.get_or_create(name="Storage Admin")
    user.groups.add(group)
    api = APIClient()
    api.force_authenticate(user=user)
    return api


@pytest.fixture
def member_client():
    """Plain authenticated member — no staff, no Storage Admin group."""
    User = get_user_model()
    user = User.objects.create_user(username="member", password="x")
    api = APIClient()
    api.force_authenticate(user=user)
    return api


class TestListEndpoint:
    def test_anonymous_is_rejected(self, client):
        ProjectStorageStintFactory()
        resp = client.get("/api/project-storage/stints/")
        assert resp.status_code in (401, 403)

    def test_plain_member_is_rejected(self, member_client):
        ProjectStorageStintFactory()
        resp = member_client.get("/api/project-storage/stints/")
        assert resp.status_code == 403

    def test_staff_can_list(self, staff_client):
        ProjectStorageStintFactory()
        resp = staff_client.get("/api/project-storage/stints/")
        assert resp.status_code == 200

    def test_storage_admin_can_list(self, storage_admin_client):
        # The whole point of the Storage Admin group: a warden volunteer
        # who isn't is_staff still sees the queue.
        ProjectStorageStintFactory()
        resp = storage_admin_client.get("/api/project-storage/stints/")
        assert resp.status_code == 200

    def test_status_filter_active(self, staff_client):
        # Active = fresh stint, nothing else set.
        ProjectStorageStintFactory(
            username="alice",
            started_at=timezone.now() - timedelta(days=2),
            expires_at=timezone.now() + timedelta(days=20),
        )
        # Removed stint that should NOT show up under active.
        ProjectStorageStintFactory(
            username="bob",
            removed_at=timezone.now() - timedelta(days=1),
        )
        resp = staff_client.get("/api/project-storage/stints/?status=active")
        assert resp.status_code == 200
        body = resp.json()
        rows = body["results"] if isinstance(body, dict) and "results" in body else body
        usernames = {r["username"] for r in rows}
        assert usernames == {"alice"}

    def test_status_filter_expiring_soon(self, staff_client):
        # Inside the 3-day warning window.
        ProjectStorageStintFactory(
            username="alice",
            started_at=timezone.now() - timedelta(days=27),
            expires_at=timezone.now() + timedelta(days=2),
        )
        # Active — outside the window.
        ProjectStorageStintFactory(
            username="bob",
            expires_at=timezone.now() + timedelta(days=20),
        )
        resp = staff_client.get("/api/project-storage/stints/?status=expiring_soon")
        body = resp.json()
        rows = body["results"] if isinstance(body, dict) and "results" in body else body
        assert {r["username"] for r in rows} == {"alice"}

    def test_status_filter_purgatory_winner_takes_terminal(self, staff_client):
        # A stint that's both expired AND in purgatory should land in
        # the purgatory bucket — terminal states win in compute_status.
        ProjectStorageStintFactory(
            username="alice",
            expires_at=timezone.now() - timedelta(days=10),
            notice_sent_at=timezone.now() - timedelta(days=8),
            moved_to_purgatory_at=timezone.now() - timedelta(days=1),
        )
        resp = staff_client.get("/api/project-storage/stints/?status=purgatory")
        body = resp.json()
        rows = body["results"] if isinstance(body, dict) and "results" in body else body
        assert {r["username"] for r in rows} == {"alice"}
        # Same stint should NOT show up under "expired".
        resp2 = staff_client.get("/api/project-storage/stints/?status=expired")
        body2 = resp2.json()
        rows2 = body2["results"] if isinstance(body2, dict) and "results" in body2 else body2
        assert not any(r["username"] == "alice" for r in rows2)

    def test_ordering_by_expires_at(self, staff_client):
        # Soonest-to-expire first when ordering=expires_at.
        ProjectStorageStintFactory(username="late", expires_at=timezone.now() + timedelta(days=20))
        ProjectStorageStintFactory(username="early", expires_at=timezone.now() + timedelta(days=2))
        resp = staff_client.get("/api/project-storage/stints/?ordering=expires_at")
        body = resp.json()
        rows = body["results"] if isinstance(body, dict) and "results" in body else body
        assert [r["username"] for r in rows[:2]] == ["early", "late"]

    def test_unknown_ordering_falls_back_to_default(self, staff_client):
        # Junk ordering value can't tank the warden queue.
        ProjectStorageStintFactory(username="z")
        resp = staff_client.get("/api/project-storage/stints/?ordering=drop+table")
        assert resp.status_code == 200


class TestMutatingActionsStayAdmin:
    """Tightening notes: send-notice / move-to-purgatory / mark-removed
    were previously matrix-listed as IsAuthenticatedOrReadOnly which the
    matrix snapshotter inferred from the class default. They're tagged
    IsAdminUser now; Storage Admin (a read-only group) must not be able
    to trigger member-facing email or write off stored work."""

    def test_storage_admin_cannot_mark_removed(self, storage_admin_client):
        stint = ProjectStorageStintFactory()
        resp = storage_admin_client.post(
            f"/api/project-storage/stints/{stint.stint_id}/mark-removed/"
        )
        assert resp.status_code == 403

    def test_staff_can_mark_removed(self, staff_client):
        stint = ProjectStorageStintFactory()
        resp = staff_client.post(f"/api/project-storage/stints/{stint.stint_id}/mark-removed/")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Generate-QR endpoint + qr_code_url serializer field
# ---------------------------------------------------------------------------


class TestGenerateQrEndpoint:
    def test_anonymous_rejected(self, client):
        stint = ProjectStorageStintFactory()
        resp = client.post(f"/api/project-storage/stints/{stint.stint_id}/generate-qr/")
        assert resp.status_code in (401, 403)

    def test_storage_admin_can_generate(self, storage_admin_client):
        # Storage Admins are trusted with the regenerate affordance —
        # it's a read-flavored operation (idempotent) and the rate
        # limiter guards against click-storms.
        stint = ProjectStorageStintFactory()
        resp = storage_admin_client.post(
            f"/api/project-storage/stints/{stint.stint_id}/generate-qr/",
            data={"include_logo": False},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        stint.refresh_from_db()
        assert bool(stint.qr_code) is True
        assert resp.json()["qr_code_url"]

    def test_member_rejected(self, member_client):
        stint = ProjectStorageStintFactory()
        resp = member_client.post(f"/api/project-storage/stints/{stint.stint_id}/generate-qr/")
        assert resp.status_code == 403

    def test_regenerate_keeps_field_populated(self, staff_client):
        stint = ProjectStorageStintFactory()
        staff_client.post(
            f"/api/project-storage/stints/{stint.stint_id}/generate-qr/",
            data={"include_logo": False},
            format="json",
        )
        staff_client.post(
            f"/api/project-storage/stints/{stint.stint_id}/generate-qr/",
            data={"include_logo": False},
            format="json",
        )
        stint.refresh_from_db()
        assert bool(stint.qr_code) is True

    def test_qr_code_url_absent_when_not_generated(self, staff_client):
        stint = ProjectStorageStintFactory()
        resp = staff_client.get(f"/api/project-storage/stints/{stint.stint_id}/")
        assert resp.status_code == 200
        assert resp.json()["qr_code_url"] is None
