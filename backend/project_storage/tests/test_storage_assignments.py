"""Staff-assigned storage: committee (C), logistics (L) and class (E).

The other lifecycle on the racking (op-wgc8, backend PR6). A member claims a
slot at the kiosk and a 30-day clock starts; these slots are handed out by
staff and stay handed out. What has to hold:

* assign → release is the whole lifecycle, and neither is open to a member;
* a slot holds one live occupancy, and the two kinds exclude each other —
  staff can't assign a slot a member is in, and a member can't claim a slot
  staff has assigned;
* releasing frees the slot without deleting the record of who had it;
* the slot readouts (occupancy, ``?occupied=``, the delete guard) count an
  assignment as holding the slot, because it does.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import IntegrityError, transaction
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from project_storage.models import StorageAssignment, StorageSlot
from project_storage.tests.factories import (
    ProjectStorageStintFactory,
    StorageAssignmentFactory,
    StorageSlotFactory,
)

pytestmark = pytest.mark.django_db

ASSIGN_URL = "/api/project-storage/assignments/assign/"
ASSIGNMENTS_URL = "/api/project-storage/assignments/"
SLOTS_URL = "/api/project-storage/slots/"
START_URL = "/api/project-storage/stints/start/"


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


@pytest.fixture
def committee():
    return Group.objects.create(name="Welding SIG")


def _assign(client, slot_code="1A1", storage_type="logistics", **extra):
    payload = {"slot": slot_code, "storage_type": storage_type}
    payload.update(extra)
    return client.post(ASSIGN_URL, payload, format="json")


def _field_errors(resp) -> dict:
    """Per-field validation messages out of the project's error envelope.

    Serializer 400s are wrapped by the shared exception handler as
    ``{"error": {"code": …, "details": {field: [msg]}}}``.
    """
    return resp.json()["error"]["details"]


# ---------------------------------------------------------------------------
# Assigning
# ---------------------------------------------------------------------------


class TestAssign:
    def test_committee_assignment_names_its_group(self, staff_client, slot, committee):
        resp = _assign(staff_client, storage_type="committee", owning_group=committee.pk)

        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["slot"] == slot.pk
        assert body["slot_code"] == "1A1"
        assert body["storage_type"] == "committee"
        assert body["type_letter"] == "C"
        assert body["owning_group_name"] == "Welding SIG"
        # The group is the occupant identity for a committee.
        assert body["occupant_display"] == "Welding SIG"
        assert body["released_at"] is None
        assert body["is_active"] is True

    def test_logistics_assignment_uses_the_free_text_label(self, staff_client, slot):
        resp = _assign(staff_client, storage_type="logistics", occupant_label="Dock crew")

        assert resp.status_code == 201, resp.content
        assert resp.json()["type_letter"] == "L"
        assert resp.json()["occupant_display"] == "Dock crew"
        # Null FK: the key must still be present (DRF drops dotted sources
        # into a null relation unless they carry a default).
        assert resp.json()["owning_group_name"] == ""

    def test_class_assignment_uses_the_free_text_label(self, staff_client, slot):
        resp = _assign(staff_client, storage_type="class", occupant_label="Ana's CNC class")

        assert resp.status_code == 201, resp.content
        assert resp.json()["type_letter"] == "E"
        assert resp.json()["occupant_display"] == "Ana's CNC class"

    def test_assign_records_who_did_it(self, staff_client, slot):
        resp = _assign(staff_client)

        assert resp.json()["assigned_by_name"] == "warden"
        assert StorageAssignment.objects.get().assigned_by.username == "warden"

    def test_assign_by_primary_key(self, staff_client, slot):
        assert _assign(staff_client, slot_code=str(slot.pk)).status_code == 201

    def test_assign_by_slot_code_field(self, staff_client, slot):
        resp = staff_client.post(
            ASSIGN_URL, {"slot_code": "1a1", "storage_type": "logistics"}, format="json"
        )
        assert resp.status_code == 201, resp.content
        assert resp.json()["slot_code"] == "1A1"

    def test_slot_is_required(self, staff_client, slot):
        resp = staff_client.post(ASSIGN_URL, {"storage_type": "logistics"}, format="json")
        assert resp.status_code == 400
        assert "slot" in _field_errors(resp)

    def test_unknown_slot_code_is_a_400(self, staff_client, slot):
        assert _assign(staff_client, slot_code="9Z9").status_code == 400

    def test_unknown_storage_type_is_a_400(self, staff_client, slot):
        resp = _assign(staff_client, storage_type="project")
        assert resp.status_code == 400
        assert "storage_type" in _field_errors(resp)

    def test_committee_must_name_the_committee(self, staff_client, slot):
        resp = _assign(staff_client, storage_type="committee")
        assert resp.status_code == 400
        assert "owning_group" in _field_errors(resp)

    def test_out_of_service_slot_cannot_be_assigned(self, staff_client, slot):
        slot.is_active = False
        slot.save(update_fields=["is_active"])

        resp = _assign(staff_client)

        assert resp.status_code == 400
        assert "out of service" in str(_field_errors(resp)["slot"])

    def test_assign_is_staff_only(self, client, slot):
        assert _assign(client).status_code in (401, 403)
        assert not StorageAssignment.objects.exists()


# ---------------------------------------------------------------------------
# One live occupancy per slot — both tables, both directions
# ---------------------------------------------------------------------------


class TestOneActiveOccupancyPerSlot:
    def test_second_assignment_on_a_held_slot_is_409(self, staff_client, slot):
        _assign(staff_client, occupant_label="Dock crew")

        resp = _assign(staff_client, storage_type="class", occupant_label="Ana's class")

        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "slot_assigned"
        assert body["slot_code"] == "1A1"
        assert body["assigned_to"] == "Dock crew"
        assert StorageAssignment.objects.count() == 1

    def test_a_different_slot_is_unaffected(self, staff_client, slot):
        StorageSlotFactory(rack=1, level="A", position=2)
        _assign(staff_client)

        assert _assign(staff_client, slot_code="1A2").status_code == 201

    def test_cannot_assign_a_slot_a_member_is_in(self, staff_client, client, slot):
        stint = ProjectStorageStintFactory(username="ada", slot=slot)

        resp = _assign(staff_client)

        assert resp.status_code == 409
        assert resp.json()["code"] == "slot_occupied"
        assert resp.json()["occupied_by"] == stint.stint_id
        assert not StorageAssignment.objects.exists()

    def test_member_cannot_claim_an_assigned_slot(self, staff_client, client, slot):
        _assign(
            staff_client, storage_type="committee", owning_group=None, occupant_label="Welding SIG"
        )

        resp = client.post(START_URL, {"username": "ada", "slot_code": "1A1"}, format="json")

        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "slot_assigned"
        assert body["assigned_to"] == "Welding SIG"
        assert body["storage_type"] == "committee"

    def test_expired_stint_still_blocks_an_assignment(self, staff_client, slot):
        # Expired-but-unresolved means the member's stuff is still physically
        # on that shelf. Staff has to resolve the stint, not paper over it.
        ProjectStorageStintFactory(
            username="ada",
            slot=slot,
            started_at=timezone.now() - timedelta(days=40),
            expires_at=timezone.now() - timedelta(days=10),
        )

        assert _assign(staff_client).status_code == 409

    def test_database_rejects_a_second_active_assignment(self, slot):
        StorageAssignmentFactory(slot=slot)

        with pytest.raises(IntegrityError), transaction.atomic():
            StorageAssignmentFactory(slot=slot)

    def test_database_allows_reuse_once_released(self, slot):
        StorageAssignmentFactory(slot=slot, released_at=timezone.now())

        # No IntegrityError: the constraint only counts unreleased rows.
        StorageAssignmentFactory(slot=slot)
        assert StorageAssignment.objects.filter(slot=slot).count() == 2

    def test_lost_race_reports_409_instead_of_500(self, staff_client, slot, monkeypatch):
        """Two wardens assign 1A1 at once: the pre-check passes for both and
        the constraint picks the winner. The loser must still get 409."""
        StorageAssignmentFactory(slot=slot, occupant_label="Dock crew")

        real = StorageAssignment.active_assignment_in_slot
        calls = {"n": 0}

        def blind_first_look(target_slot):
            # First call = the pre-check the racing request "wins"; the
            # recovery lookup after the IntegrityError sees the truth.
            calls["n"] += 1
            return None if calls["n"] == 1 else real(target_slot)

        monkeypatch.setattr(StorageAssignment, "active_assignment_in_slot", blind_first_look)

        resp = _assign(staff_client, storage_type="class", occupant_label="Ana's class")

        assert calls["n"] == 2
        assert resp.status_code == 409
        assert resp.json()["code"] == "slot_assigned"
        assert resp.json()["assigned_to"] == "Dock crew"
        assert StorageAssignment.objects.count() == 1


# ---------------------------------------------------------------------------
# Releasing
# ---------------------------------------------------------------------------


class TestRelease:
    def test_release_stamps_released_at_and_keeps_the_row(self, staff_client, slot):
        assignment = StorageAssignmentFactory(slot=slot)

        resp = staff_client.post(f"{ASSIGNMENTS_URL}{assignment.pk}/release/", {}, format="json")

        assert resp.status_code == 200, resp.content
        assert resp.json()["released_at"] is not None
        assert resp.json()["is_active"] is False
        assignment.refresh_from_db()
        # The history of who held 1A1 survives — the slot is freed by the row
        # stopping to match "active", not by deleting it.
        assert assignment.slot_id == slot.pk

    def test_releasing_frees_the_slot_for_the_next_assignment(self, staff_client, slot):
        assignment = StorageAssignmentFactory(slot=slot)
        staff_client.post(f"{ASSIGNMENTS_URL}{assignment.pk}/release/", {}, format="json")

        assert _assign(staff_client, storage_type="class", occupant_label="Ana").status_code == 201

    def test_releasing_frees_the_slot_for_the_kiosk(self, staff_client, client, slot):
        assignment = StorageAssignmentFactory(slot=slot)
        staff_client.post(f"{ASSIGNMENTS_URL}{assignment.pk}/release/", {}, format="json")

        resp = client.post(START_URL, {"username": "ada", "slot_code": "1A1"}, format="json")
        assert resp.status_code == 201, resp.content

    def test_releasing_twice_is_a_409(self, staff_client, slot):
        assignment = StorageAssignmentFactory(slot=slot, released_at=timezone.now())

        resp = staff_client.post(f"{ASSIGNMENTS_URL}{assignment.pk}/release/", {}, format="json")

        assert resp.status_code == 409
        assert resp.json()["code"] == "already_released"

    def test_release_is_staff_only(self, client, slot):
        assignment = StorageAssignmentFactory(slot=slot)

        assert client.post(f"{ASSIGNMENTS_URL}{assignment.pk}/release/").status_code in (401, 403)
        assignment.refresh_from_db()
        assert assignment.released_at is None


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


class TestAssignmentList:
    @pytest.fixture(autouse=True)
    def _fixtures(self, slot):
        self.live = StorageAssignmentFactory(slot=slot, storage_type="committee")
        self.released = StorageAssignmentFactory(
            slot=StorageSlotFactory(rack=2, level="B", position=1),
            storage_type="class",
            released_at=timezone.now(),
        )

    def _codes(self, resp):
        rows = resp.json()
        rows = rows["results"] if isinstance(rows, dict) else rows
        return {row["slot_code"] for row in rows}

    def test_lists_every_assignment_by_default(self, staff_client):
        assert self._codes(staff_client.get(ASSIGNMENTS_URL)) == {"1A1", "2B1"}

    def test_active_filter(self, staff_client):
        assert self._codes(staff_client.get(f"{ASSIGNMENTS_URL}?active=true")) == {"1A1"}
        assert self._codes(staff_client.get(f"{ASSIGNMENTS_URL}?active=false")) == {"2B1"}

    def test_storage_type_filter(self, staff_client):
        assert self._codes(staff_client.get(f"{ASSIGNMENTS_URL}?storage_type=class")) == {"2B1"}

    def test_rack_filter(self, staff_client):
        assert self._codes(staff_client.get(f"{ASSIGNMENTS_URL}?rack=2")) == {"2B1"}

    def test_junk_rack_matches_nothing(self, staff_client):
        assert self._codes(staff_client.get(f"{ASSIGNMENTS_URL}?rack=x")) == set()

    def test_slot_code_filter_is_case_insensitive(self, staff_client):
        assert self._codes(staff_client.get(f"{ASSIGNMENTS_URL}?slot_code=1a1")) == {"1A1"}

    def test_list_is_staff_only(self, client):
        assert client.get(ASSIGNMENTS_URL).status_code in (401, 403)

    def test_list_does_not_query_per_row(self, staff_client, django_assert_max_num_queries):
        for position in range(2, 8):
            StorageAssignmentFactory(
                slot=StorageSlotFactory(rack=1, level="A", position=position),
                storage_type="committee",
                owning_group=Group.objects.create(name=f"SIG {position}"),
            )
        # select_related covers slot/owning_group/assigned_by, so the row
        # count doesn't move the query count.
        with django_assert_max_num_queries(6):
            staff_client.get(ASSIGNMENTS_URL)


# ---------------------------------------------------------------------------
# The slot readouts count an assignment as occupancy
# ---------------------------------------------------------------------------


class TestSlotSeesAssignments:
    def test_slot_reports_its_assignment(self, staff_client, slot, committee):
        StorageAssignmentFactory(slot=slot, storage_type="committee", owning_group=committee)

        body = staff_client.get(f"{SLOTS_URL}1A1/").json()

        assert body["is_occupied"] is True
        assert body["occupancy_type"] == "C"
        assert body["current_stint"] is None
        assert body["current_assignment"]["occupant_display"] == "Welding SIG"

    def test_free_slot_reports_neither(self, staff_client, slot):
        body = staff_client.get(f"{SLOTS_URL}1A1/").json()

        assert body["is_occupied"] is False
        assert body["occupancy_type"] is None
        assert body["current_assignment"] is None

    def test_released_assignment_stops_showing(self, staff_client, slot):
        StorageAssignmentFactory(slot=slot, released_at=timezone.now())

        body = staff_client.get(f"{SLOTS_URL}1A1/").json()

        assert body["is_occupied"] is False
        assert body["current_assignment"] is None

    def test_occupied_filter_counts_assignments(self, staff_client, slot):
        free = StorageSlotFactory(rack=1, level="A", position=2)
        stinted = StorageSlotFactory(rack=1, level="A", position=3)
        ProjectStorageStintFactory(username="ada", slot=stinted)
        StorageAssignmentFactory(slot=slot)
        # A slot-less stint is the NOT IN (NULL) trap: it must not make the
        # "free slots" half of the filter come back empty.
        ProjectStorageStintFactory(username="bo", slot=None)

        def codes(query):
            rows = staff_client.get(f"{SLOTS_URL}?{query}").json()
            rows = rows["results"] if isinstance(rows, dict) else rows
            return {row["code"] for row in rows}

        assert codes("occupied=true") == {"1A1", "1A3"}
        assert codes("occupied=false") == {free.code}

    def test_delete_is_refused_while_assigned(self, staff_client, slot):
        StorageAssignmentFactory(slot=slot, occupant_label="Dock crew")

        resp = staff_client.delete(f"{SLOTS_URL}1A1/")

        assert resp.status_code == 409
        assert resp.json()["code"] == "slot_assigned"
        # CASCADE would have taken the assignment record with it.
        assert StorageAssignment.objects.filter(slot=slot).exists()

    def test_delete_works_once_released(self, staff_client, slot):
        StorageAssignmentFactory(slot=slot, released_at=timezone.now())

        assert staff_client.delete(f"{SLOTS_URL}1A1/").status_code == 204

    def test_slot_occupancy_does_not_query_per_row(
        self, staff_client, django_assert_max_num_queries
    ):
        for position in range(1, 8):
            other = StorageSlotFactory(rack=1, level="A", position=position)
            StorageAssignmentFactory(
                slot=other, owning_group=Group.objects.create(name=f"S{position}")
            )
        # Both occupancy prefetches are one query each regardless of how many
        # slots the page holds; owning_group rides along in the prefetch.
        with django_assert_max_num_queries(8):
            staff_client.get(SLOTS_URL)


# ---------------------------------------------------------------------------
# Model-level behaviour
# ---------------------------------------------------------------------------


class TestAssignmentModel:
    def test_type_letters_cover_every_choice(self):
        assert set(StorageAssignment.TYPE_LETTERS) == {
            value for value, _ in StorageAssignment.STORAGE_TYPE_CHOICES
        }
        assert sorted(StorageAssignment.TYPE_LETTERS.values()) == ["C", "E", "L"]

    def test_occupant_display_falls_back_to_the_type(self, slot):
        assignment = StorageAssignmentFactory(slot=slot, occupant_label="")
        assert assignment.occupant_display == "Logistics"

    def test_a_stint_wins_a_double_occupancy(self, slot):
        """The invariant spans two tables, so nothing at the DB stops both
        rows existing (a fixture, a shell). The member's clock wins."""
        StorageAssignmentFactory(slot=slot)
        ProjectStorageStintFactory(username="ada", slot=slot)

        slot.refresh_from_db()
        assert slot.occupancy_type == "P"

    def test_current_assignment_reads_the_prefetch(self, slot, django_assert_num_queries):
        StorageAssignmentFactory(slot=slot)

        prefetched = StorageSlot.objects.prefetch_related(
            StorageSlot.active_assignments_prefetch()
        ).get(pk=slot.pk)

        with django_assert_num_queries(0):
            # occupant_display walks owning_group — select_related in the
            # prefetch keeps that free too.
            assert prefetched.current_assignment is not None
            assert prefetched.current_assignment.occupant_display

    def test_current_assignment_falls_back_to_a_lookup(self, slot):
        StorageAssignmentFactory(slot=slot)
        assert slot.current_assignment is not None
