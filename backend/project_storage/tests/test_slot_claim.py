"""Slot-aware claims: scanning a slot's code reserves it, one stint at a time.

The flow this covers (op-hfw5, backend PR3): a member scans the marker on a
rack upright and the kiosk POSTs the code to ``stints/start/``. That claim is
immediate and self-service — there is no warden approval step — so the guards
have to be right at the API:

* the slot becomes the stint's location, beating the free-text field;
* a slot holds one live stint at a time (409 ``slot_occupied``);
* resolving a stint (removed / purgatory) frees its slot for the next member;
* stints with no slot keep working exactly as they did before racking existed.
"""

from __future__ import annotations

from datetime import timedelta
from io import BytesIO

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

import pytest
from PIL import Image
from rest_framework.test import APIClient

from project_storage.models import ProjectStorageEvent, ProjectStorageStint
from project_storage.services.label_service import (
    BROTHER_SIZE_PX,
    EPSON_SIZE_PX,
    render_stint_label,
    ticket_lines,
)
from project_storage.tests.factories import ProjectStorageStintFactory, StorageSlotFactory

pytestmark = pytest.mark.django_db

START_URL = "/api/project-storage/stints/start/"
SLOTS_URL = "/api/project-storage/slots/"


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


@pytest.fixture
def slot():
    return StorageSlotFactory(rack=1, level="A", position=1)


def _claim(client, slot_code: str, username: str = "newbie", **extra):
    payload = {"username": username, "slot_code": slot_code}
    payload.update(extra)
    return client.post(START_URL, payload, format="json")


# ---------------------------------------------------------------------------
# Claiming a slot
# ---------------------------------------------------------------------------


class TestClaimIntoSlot:
    def test_claim_by_code_sets_the_slot_as_the_location(self, client, slot):
        resp = _claim(client, "1A1", project_title="Restore Schwinn")

        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["slot"] == slot.pk
        assert body["slot_code"] == "1A1"
        # The slot is where this stint lives — no warden step in between.
        assert body["location_display"] == "1A1"

        stint = ProjectStorageStint.objects.get(stint_id=body["stint_id"])
        assert stint.slot_id == slot.pk

    def test_claim_by_primary_key(self, client, slot):
        """The warden console has the row, not the printed code."""
        resp = client.post(START_URL, {"username": "newbie", "slot": slot.pk}, format="json")

        assert resp.status_code == 201, resp.content
        assert resp.json()["slot_code"] == "1A1"

    def test_claim_by_code_in_the_slot_field(self, client, slot):
        """The slot's own QR sends members to ``…/kiosk?slot=1A1``, so the
        kiosk hands the code straight back under the name ``slot``. A code
        always has a letter in it and a pk never does — no ambiguity."""
        resp = client.post(START_URL, {"username": "newbie", "slot": "1A1"}, format="json")

        assert resp.status_code == 201, resp.content
        assert resp.json()["slot"] == slot.pk

    def test_claim_code_is_case_insensitive(self, client, slot):
        # Wedge scanners and hand-typed codes both show up lower-case.
        assert _claim(client, "1a1").json()["slot_code"] == "1A1"

    def test_unknown_slot_id_is_a_400(self, client, slot):
        resp = client.post(START_URL, {"username": "newbie", "slot": slot.pk + 999}, format="json")
        assert resp.status_code == 400
        assert ProjectStorageStint.objects.count() == 0

    def test_slot_value_that_is_neither_id_nor_code_is_a_400(self, client):
        resp = client.post(START_URL, {"username": "newbie", "slot": "shelf A"}, format="json")
        assert resp.status_code == 400
        assert ProjectStorageStint.objects.count() == 0

    def test_claim_records_the_slot_on_the_created_event(self, client, slot):
        stint_id = _claim(client, "1A1").json()["stint_id"]
        event = ProjectStorageEvent.objects.get(
            stint__stint_id=stint_id, event_type=ProjectStorageEvent.EVENT_CREATED
        )
        assert "1A1" in event.note

    def test_slot_beats_free_text_when_both_are_sent(self, client, slot):
        resp = _claim(client, "1A1", storage_location_name="Shelf A")

        body = resp.json()
        # Both are stored — the free text is still the member's own words —
        # but the surveyed slot is what location_display reports.
        assert body["storage_location_name"] == "Shelf A"
        assert body["location_display"] == "1A1"

    def test_unknown_code_is_a_400(self, client):
        resp = _claim(client, "9Z9")
        assert resp.status_code == 400
        assert ProjectStorageStint.objects.count() == 0

    def test_malformed_code_is_a_400(self, client):
        resp = _claim(client, "not-a-slot")
        assert resp.status_code == 400
        assert ProjectStorageStint.objects.count() == 0

    def test_slot_and_slot_code_must_agree(self, client, slot):
        other = StorageSlotFactory(rack=2, level="A", position=1)
        resp = client.post(
            START_URL,
            {"username": "newbie", "slot": other.pk, "slot_code": "1A1"},
            format="json",
        )
        assert resp.status_code == 400
        assert ProjectStorageStint.objects.count() == 0

    def test_out_of_service_slot_cannot_be_claimed(self, client, slot):
        # is_active=False means "on file, not offered for new reservations".
        slot.is_active = False
        slot.save()

        resp = _claim(client, "1A1")
        assert resp.status_code == 400
        assert ProjectStorageStint.objects.count() == 0


# ---------------------------------------------------------------------------
# One live stint per slot
# ---------------------------------------------------------------------------


class TestOneActiveStintPerSlot:
    def test_second_claim_on_a_taken_slot_is_409(self, client, slot):
        first = _claim(client, "1A1", username="ada", first_name="Ada")
        assert first.status_code == 201

        resp = _claim(client, "1A1", username="bob")

        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "slot_occupied"
        assert body["slot_code"] == "1A1"
        # Enough for the kiosk to say who to go find.
        assert body["occupied_by"] == first.json()["stint_id"]
        assert ProjectStorageStint.objects.filter(username="bob").count() == 0

    def test_a_different_slot_is_unaffected(self, client, slot):
        StorageSlotFactory(rack=1, level="A", position=2)
        assert _claim(client, "1A1", username="ada").status_code == 201
        assert _claim(client, "1A2", username="bob").status_code == 201

    def test_expired_but_unresolved_stint_still_holds_its_slot(self, client, slot):
        """The shelf is physically still full — same rule as the per-member
        guard, where an expired stint also still counts."""
        ProjectStorageStintFactory(
            username="ada",
            slot=slot,
            started_at=timezone.now() - timedelta(days=45),
            expires_at=timezone.now() - timedelta(days=15),
        )
        resp = _claim(client, "1A1", username="bob")
        assert resp.status_code == 409
        assert resp.json()["code"] == "slot_occupied"

    def test_database_rejects_a_second_active_stint_in_one_slot(self, slot):
        """The API pre-check is a courtesy; the constraint is the guarantee."""
        ProjectStorageStintFactory(username="ada", slot=slot)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ProjectStorageStintFactory(username="bob", slot=slot)

    def test_database_allows_reuse_once_the_first_stint_is_resolved(self, slot):
        ProjectStorageStintFactory(username="ada", slot=slot, removed_at=timezone.now())
        ProjectStorageStintFactory(username="bob", slot=slot, moved_to_purgatory_at=timezone.now())
        # Two historical occupants plus one live one is fine.
        ProjectStorageStintFactory(username="cass", slot=slot)
        assert ProjectStorageStint.objects.filter(slot=slot).count() == 3

    def test_slot_less_stints_do_not_collide(self):
        """The constraint is partial — NULL slots aren't a shared slot."""
        ProjectStorageStintFactory(username="ada", slot=None)
        ProjectStorageStintFactory(username="bob", slot=None)
        assert ProjectStorageStint.objects.filter(slot__isnull=True).count() == 2

    def test_lost_race_reports_409_instead_of_500(self, client, slot, monkeypatch):
        """Two kiosks submit at once: the pre-check passes for both and the
        constraint picks the winner. The loser must still get slot_occupied."""
        ProjectStorageStintFactory(username="ada", slot=slot)

        real = ProjectStorageStint.active_stint_in_slot
        calls = {"n": 0}

        def blind_first_look(target_slot):
            # First call = the pre-check the racing request "wins"; the
            # recovery lookup after the IntegrityError sees the truth.
            calls["n"] += 1
            return None if calls["n"] == 1 else real(target_slot)

        monkeypatch.setattr(ProjectStorageStint, "active_stint_in_slot", blind_first_look)

        resp = _claim(client, "1A1", username="bob")

        assert calls["n"] == 2
        assert resp.status_code == 409
        assert resp.json()["code"] == "slot_occupied"
        assert ProjectStorageStint.objects.filter(username="bob").count() == 0


# ---------------------------------------------------------------------------
# Resolving a stint frees its slot
# ---------------------------------------------------------------------------


class TestSlotIsFreedOnResolution:
    def test_mark_removed_frees_the_slot(self, client, staff_client, slot):
        stint_id = _claim(client, "1A1", username="ada").json()["stint_id"]
        assert (
            staff_client.post(f"/api/project-storage/stints/{stint_id}/mark-removed/").status_code
            == 200
        )

        # Next member walks up and claims the now-empty slot.
        assert _claim(client, "1A1", username="bob").status_code == 201
        # ...and the history of who was there survives.
        assert ProjectStorageStint.objects.get(stint_id=stint_id).slot_id == slot.pk

    def test_move_to_purgatory_frees_the_slot(self, client, staff_client, slot):
        stint = ProjectStorageStintFactory(
            username="ada",
            slot=slot,
            started_at=timezone.now() - timedelta(days=45),
            expires_at=timezone.now() - timedelta(days=15),
            notice_sent_at=timezone.now() - timedelta(days=8),
        )
        resp = staff_client.post(
            f"/api/project-storage/stints/{stint.stint_id}/move-to-purgatory/",
            {"purgatory_location_name": "Project Purgatory"},
            format="json",
        )
        assert resp.status_code == 200, resp.content

        assert _claim(client, "1A1", username="bob").status_code == 201
        stint.refresh_from_db()
        assert stint.slot_id == slot.pk

    def test_purgatory_event_names_the_vacated_slot(self, staff_client, slot):
        stint = ProjectStorageStintFactory(
            username="ada", slot=slot, notice_sent_at=timezone.now() - timedelta(days=8)
        )
        staff_client.post(
            f"/api/project-storage/stints/{stint.stint_id}/move-to-purgatory/",
            {"purgatory_location_name": "Project Purgatory"},
            format="json",
        )
        event = stint.events.get(event_type=ProjectStorageEvent.EVENT_MOVED_TO_PURGATORY)
        assert "Project Purgatory" in event.note
        assert "1A1" in event.note

    def test_purgatory_note_is_unchanged_for_a_slot_less_stint(self, staff_client):
        stint = ProjectStorageStintFactory(
            username="ada", slot=None, notice_sent_at=timezone.now() - timedelta(days=8)
        )
        staff_client.post(
            f"/api/project-storage/stints/{stint.stint_id}/move-to-purgatory/",
            {"purgatory_location_name": "Project Purgatory"},
            format="json",
        )
        event = stint.events.get(event_type=ProjectStorageEvent.EVENT_MOVED_TO_PURGATORY)
        assert event.note == "Project Purgatory"


# ---------------------------------------------------------------------------
# The pre-racking paths still work
# ---------------------------------------------------------------------------


class TestNonRackStorageUnchanged:
    def test_free_text_claim_still_works(self, client):
        resp = client.post(
            START_URL,
            {"username": "newbie", "storage_location_name": "Shelf A"},
            format="json",
        )
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["slot"] is None
        # The key is present, not dropped: a null FK is exactly the case
        # where a dotted-source serializer field would vanish.
        assert body["slot_code"] == ""
        assert body["location_display"] == "Shelf A"

    def test_claim_with_no_location_at_all_still_works(self, client):
        resp = client.post(START_URL, {"username": "newbie"}, format="json")
        assert resp.status_code == 201, resp.content
        assert resp.json()["location_display"] == ""

    def test_member_guard_still_fires_for_a_slot_claim(self, client, slot):
        """One stint per member outranks having a free slot to put it in."""
        ProjectStorageStintFactory(username="busy")
        resp = _claim(client, "1A1", username="busy")
        assert resp.status_code == 409
        assert resp.json()["code"] == "active_stint_exists"

    def test_cooldown_still_fires_for_a_slot_claim(self, client, slot):
        ProjectStorageStintFactory(
            username="cooling", removed_at=timezone.now() - timedelta(hours=12)
        )
        resp = _claim(client, "1A1", username="cooling")
        assert resp.status_code == 409
        assert resp.json()["code"] == "cooldown_active"


class TestClaimIdempotency:
    def test_retrying_the_same_scan_returns_the_same_stint(self, client, slot):
        """A network blip mid-claim must not bounce the member off their own
        stint as slot_occupied."""
        first = _claim(client, "1A1")
        assert first.status_code == 201

        second = _claim(client, "1A1")

        assert second.status_code == 200, second.content
        assert second.json()["stint_id"] == first.json()["stint_id"]
        assert ProjectStorageStint.objects.count() == 1

    def test_retry_by_pk_matches_a_claim_made_by_code(self, client, slot):
        first = _claim(client, "1A1")
        second = client.post(START_URL, {"username": "newbie", "slot": slot.pk}, format="json")
        assert second.status_code == 200, second.content
        assert second.json()["stint_id"] == first.json()["stint_id"]

    def test_a_different_slot_is_not_a_retry(self, client, slot):
        StorageSlotFactory(rack=1, level="A", position=2)
        assert _claim(client, "1A1").status_code == 201
        # Same member, same everything except the slot — that's a new
        # intent, so the standard one-stint-per-member 409 fires.
        resp = _claim(client, "1A2")
        assert resp.status_code == 409
        assert resp.json()["code"] == "active_stint_exists"


# ---------------------------------------------------------------------------
# Warden visibility: which slots are free, and who is in the rest
# ---------------------------------------------------------------------------


class TestSlotOccupancyReadout:
    def test_free_slot_reports_no_occupant(self, staff_client, slot):
        resp = staff_client.get(f"{SLOTS_URL}1A1/")
        assert resp.data["is_occupied"] is False
        assert resp.data["current_stint"] is None

    def test_occupied_slot_reports_who_is_in_it(self, staff_client, client, slot):
        stint_id = _claim(client, "1A1", username="ada", first_name="Ada", last_name="L").json()[
            "stint_id"
        ]

        resp = staff_client.get(f"{SLOTS_URL}1A1/")

        assert resp.data["is_occupied"] is True
        occupant = resp.data["current_stint"]
        assert occupant["stint_id"] == stint_id
        assert occupant["username"] == "ada"
        assert occupant["display_name"] == "Ada L"
        assert occupant["status"] == "active"

    def test_resolved_stint_stops_showing_as_the_occupant(self, staff_client, slot):
        ProjectStorageStintFactory(username="ada", slot=slot, removed_at=timezone.now())
        resp = staff_client.get(f"{SLOTS_URL}1A1/")
        assert resp.data["is_occupied"] is False
        assert resp.data["current_stint"] is None

    def test_newly_created_slot_reports_occupancy_too(self, staff_client):
        """The create response has no prefetch to read — the serializer has
        to fall back rather than blow up or omit the field."""
        resp = staff_client.post(SLOTS_URL, {"rack": 4, "level": "A", "position": 1}, format="json")
        assert resp.status_code == 201, resp.data
        assert resp.data["is_occupied"] is False
        assert resp.data["current_stint"] is None


class TestSlotCurrentStintProperty:
    def test_reads_the_prefetch_without_extra_queries(self, slot, django_assert_num_queries):
        from project_storage.models import StorageSlot

        ProjectStorageStintFactory(username="ada", slot=slot)
        slots = list(
            StorageSlot.objects.filter(pk=slot.pk).prefetch_related(
                StorageSlot.active_stints_prefetch()
            )
        )
        with django_assert_num_queries(0):
            assert slots[0].current_stint.username == "ada"

    def test_falls_back_to_a_lookup_without_one(self, slot):
        stint = ProjectStorageStintFactory(username="ada", slot=slot)
        assert slot.current_stint == stint

    def test_admin_column_names_the_occupant(self, slot):
        from django.contrib.admin.sites import AdminSite

        from project_storage.admin import StorageSlotAdmin
        from project_storage.models import StorageSlot

        admin = StorageSlotAdmin(StorageSlot, AdminSite())
        assert admin.occupied_by(slot) == "—"

        stint = ProjectStorageStintFactory(
            username="ada", first_name="Ada", last_name="L", slot=slot
        )
        # Fresh instance: the property caches per instance, and the warden
        # is looking at a changelist row, not this one.
        row = StorageSlot.objects.get(pk=slot.pk)
        assert admin.occupied_by(row) == f"{stint.stint_id} · Ada L"


class TestSlotOccupancyFilter:
    @pytest.fixture(autouse=True)
    def _rack(self):
        self.taken = StorageSlotFactory(rack=1, level="A", position=1)
        self.free = StorageSlotFactory(rack=1, level="A", position=2)
        self.was_taken = StorageSlotFactory(rack=1, level="A", position=3)
        ProjectStorageStintFactory(username="ada", slot=self.taken)
        ProjectStorageStintFactory(username="bob", slot=self.was_taken, removed_at=timezone.now())
        # A stint with no slot at all: its NULL must not swallow the
        # "free slots" half of the filter.
        ProjectStorageStintFactory(username="cass", slot=None)

    def _codes(self, resp):
        rows = resp.data["results"] if isinstance(resp.data, dict) else resp.data
        return sorted(row["code"] for row in rows)

    def test_occupied_true_lists_only_slots_in_use(self, staff_client):
        assert self._codes(staff_client.get(f"{SLOTS_URL}?occupied=true")) == ["1A1"]

    def test_occupied_false_lists_the_handout_candidates(self, staff_client):
        assert self._codes(staff_client.get(f"{SLOTS_URL}?occupied=false")) == ["1A2", "1A3"]

    def test_no_filter_lists_everything(self, staff_client):
        assert self._codes(staff_client.get(SLOTS_URL)) == ["1A1", "1A2", "1A3"]

    def test_occupancy_filter_composes_with_the_others(self, staff_client):
        StorageSlotFactory(rack=2, level="A", position=1)
        assert self._codes(staff_client.get(f"{SLOTS_URL}?rack=1&occupied=false")) == [
            "1A2",
            "1A3",
        ]

    def test_occupancy_does_not_query_per_row(self, staff_client, django_assert_max_num_queries):
        # Occupancy costs one prefetch for the page, not one query per slot.
        with django_assert_max_num_queries(7):
            resp = staff_client.get(SLOTS_URL)
        assert len(self._codes(resp)) == 3


class TestDeletingAnOccupiedSlot:
    def test_delete_is_refused_while_a_stint_is_in_it(self, staff_client, slot):
        stint = ProjectStorageStintFactory(username="ada", slot=slot)

        resp = staff_client.delete(f"{SLOTS_URL}1A1/")

        assert resp.status_code == 409
        assert resp.json()["code"] == "slot_occupied"
        assert resp.json()["occupied_by"] == stint.stint_id
        # The slot — and the live stint's location — survive.
        stint.refresh_from_db()
        assert stint.slot_id == slot.pk

    def test_delete_works_once_the_slot_is_free(self, staff_client, slot):
        ProjectStorageStintFactory(username="ada", slot=slot, removed_at=timezone.now())
        assert staff_client.delete(f"{SLOTS_URL}1A1/").status_code == 204

    def test_deleting_a_free_slot_nulls_only_historical_links(self, staff_client, slot):
        old = ProjectStorageStintFactory(username="ada", slot=slot, removed_at=timezone.now())
        staff_client.delete(f"{SLOTS_URL}1A1/")
        old.refresh_from_db()
        # SET_NULL: the stint row (and its audit log) outlives the racking.
        assert old.slot_id is None


class TestClaimTicketShowsTheSlot:
    """The printed ticket is what the member walks away with — it has to say
    which slot they just claimed, or they'll be back asking the warden."""

    def test_ticket_lines_include_the_slot_code(self, slot):
        stint = ProjectStorageStintFactory(
            username="ada", first_name="Ada", last_name="L", slot=slot
        )
        assert ticket_lines(stint) == [
            stint.stint_id,
            "Slot 1A1",
            "Ada L",
            "Proj: Test project",
        ]

    def test_ticket_lines_are_unchanged_without_a_slot(self):
        stint = ProjectStorageStintFactory(username="ada", first_name="Ada", last_name="L")
        assert ticket_lines(stint) == [stint.stint_id, "Ada L", "Proj: Test project"]

    @pytest.mark.parametrize("printer", ["brother_ql", "epson_tm"])
    def test_the_code_reaches_the_rendered_png(self, printer, slot):
        stint = ProjectStorageStintFactory(username="ada", slot=slot)
        with_slot = render_stint_label(stint, printer=printer)

        stint.slot = None
        without_slot = render_stint_label(stint, printer=printer)

        assert with_slot[:8] == b"\x89PNG\r\n\x1a\n"
        # No OCR in this environment; different pixels for the same stint is
        # the honest check that the extra line is actually drawn.
        assert with_slot != without_slot

    @pytest.mark.parametrize(
        "printer,size", [("brother_ql", BROTHER_SIZE_PX), ("epson_tm", EPSON_SIZE_PX)]
    )
    def test_the_extra_line_does_not_resize_the_label(self, printer, size, slot):
        # Media width/length are fixed by the printer, so the slot line has
        # to fit inside the existing block rather than grow the canvas.
        stint = ProjectStorageStintFactory(username="ada", slot=slot)
        img = Image.open(BytesIO(render_stint_label(stint, printer=printer)))
        assert img.size == size

    def test_label_endpoint_serves_a_slot_stint(self, client, slot):
        stint = ProjectStorageStintFactory(username="ada", slot=slot)
        resp = client.get(f"/api/project-storage/stints/{stint.stint_id}/label/")
        assert resp.status_code == 200
        assert resp["Content-Type"] == "image/png"


class TestStintListExposesSlot:
    def test_list_reports_slot_code_without_a_query_per_row(
        self, staff_client, django_assert_max_num_queries
    ):
        for position in (1, 2, 3):
            ProjectStorageStintFactory(
                username=f"m{position}",
                slot=StorageSlotFactory(rack=1, level="A", position=position),
            )
        with django_assert_max_num_queries(7):
            resp = staff_client.get("/api/project-storage/stints/")
        rows = resp.data["results"] if isinstance(resp.data, dict) else resp.data
        assert sorted(row["slot_code"] for row in rows) == ["1A1", "1A2", "1A3"]

    def test_by_member_reports_the_slot(self, staff_client, slot):
        ProjectStorageStintFactory(username="ada", slot=slot)
        resp = staff_client.get("/api/project-storage/stints/by-member/ada/")
        assert [row["slot_code"] for row in resp.json()] == ["1A1"]
