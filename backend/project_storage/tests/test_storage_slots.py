"""Storage-slot racking: codes, uniqueness, the bulk generator, and the API.

The physical scheme under test: ``<rack><level><position>`` — 1A1 is rack 1,
level A (ground-reachable), first position counting from South/East.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction

import pytest
from rest_framework.test import APIClient

from fiducials.models import FAMILY_CAPACITY, FAMILY_TAG36H11, AprilTagAssignment
from fiducials.services.allocator import allocate_tag, get_active_tag_id, resolve_subject
from project_storage.models import StorageSlot
from project_storage.permissions import STORAGE_ADMIN_GROUP
from project_storage.services.storage_slots import (
    SLOT_TAG_FAMILY,
    LevelSpec,
    ensure_slot_tag,
    generate_rack_slots,
)
from project_storage.tests.factories import ProjectStorageStintFactory, StorageSlotFactory

pytestmark = pytest.mark.django_db

SLOTS_URL = "/api/project-storage/slots/"


@pytest.fixture
def staff_client():
    User = get_user_model()
    user = User.objects.create_user(username="warden", password="x", is_staff=True)
    api = APIClient()
    api.force_authenticate(user=user)
    return api


# ---------------------------------------------------------------------------
# code <-> components
# ---------------------------------------------------------------------------


class TestSlotCode:
    @pytest.mark.parametrize(
        "rack,level,position,code",
        [
            (1, "A", 1, "1A1"),
            (1, "B", 3, "1B3"),
            (2, "A", 2, "2A2"),
            (12, "Z", 40, "12Z40"),
        ],
    )
    def test_compose_and_parse_round_trip(self, rack, level, position, code):
        assert StorageSlot.compose_code(rack, level, position) == code
        assert StorageSlot.parse_code(code) == (rack, level, position)

    def test_code_is_computed_on_save(self):
        slot = StorageSlot.objects.create(rack=1, level="B", position=3)
        assert slot.code == "1B3"

    def test_level_is_normalized_to_upper_case(self):
        slot = StorageSlot.objects.create(rack=1, level="c", position=4)
        assert slot.level == "C"
        assert slot.code == "1C4"
        assert StorageSlot.parse_code("1c4") == (1, "C", 4)

    def test_code_follows_a_component_edit(self):
        slot = StorageSlot.objects.create(rack=1, level="A", position=1)
        slot.position = 7
        slot.save()
        slot.refresh_from_db()
        assert slot.code == "1A7"

    def test_code_follows_a_component_edit_via_update_fields(self):
        """A narrow save() must not leave the stored code behind."""
        slot = StorageSlot.objects.create(rack=1, level="A", position=1)
        slot.position = 9
        slot.save(update_fields=["position"])
        slot.refresh_from_db()
        assert slot.code == "1A9"

    @pytest.mark.parametrize("bad", ["", "A1", "1A", "11", "1AB2", "1-A-1", "1 A 1", None])
    def test_parse_rejects_malformed_codes(self, bad):
        with pytest.raises(ValueError):
            StorageSlot.parse_code(bad)

    def test_level_must_be_a_single_letter(self):
        slot = StorageSlot(rack=1, level="1", position=1)
        with pytest.raises(ValidationError):
            slot.full_clean()

    def test_rack_and_position_start_at_one(self):
        with pytest.raises(ValidationError):
            StorageSlot(rack=0, level="A", position=1).full_clean()
        with pytest.raises(ValidationError):
            StorageSlot(rack=1, level="A", position=0).full_clean()


class TestSlotUniqueness:
    def test_duplicate_components_rejected(self):
        StorageSlot.objects.create(rack=1, level="A", position=1)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                StorageSlot.objects.create(rack=1, level="A", position=1)

    def test_duplicate_code_rejected(self):
        """save() derives the code, so a collision can only come in through a
        raw UPDATE — the column's own uniqueness is what makes a scanned code
        resolve to exactly one slot."""
        StorageSlot.objects.create(rack=1, level="A", position=1)
        other = StorageSlot.objects.create(rack=1, level="A", position=2)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                StorageSlot.objects.filter(pk=other.pk).update(code="1A1")

    def test_ordering_is_by_rack_level_position(self):
        StorageSlotFactory(rack=2, level="A", position=1)
        StorageSlotFactory(rack=1, level="B", position=1)
        StorageSlotFactory(rack=1, level="A", position=2)
        StorageSlotFactory(rack=1, level="A", position=1)
        assert [s.code for s in StorageSlot.objects.all()] == ["1A1", "1A2", "1B1", "2A1"]


# ---------------------------------------------------------------------------
# AprilTag foundation
# ---------------------------------------------------------------------------


class TestSlotTags:
    def test_ensure_slot_tag_allocates_from_the_location_family(self):
        slot = StorageSlotFactory()
        assignment = ensure_slot_tag(slot)
        assert assignment is not None
        assert assignment.family == SLOT_TAG_FAMILY
        assert assignment.family != FAMILY_TAG36H11  # not the recycling pool

    def test_ensure_slot_tag_is_idempotent(self):
        slot = StorageSlotFactory()
        first = ensure_slot_tag(slot)
        again = ensure_slot_tag(slot)
        assert first.pk == again.pk
        assert AprilTagAssignment.objects.filter(object_id=slot.pk).count() == 1

    def test_slot_tags_do_not_consume_the_stint_pool(self):
        """A slot's ID and a stint's ID are drawn from different families, so
        both can be 0 at once and neither starves the other."""
        slot_tag = ensure_slot_tag(StorageSlotFactory())
        stint_tag = allocate_tag(ProjectStorageStintFactory())
        assert slot_tag.tag_id == stint_tag.tag_id == 0
        assert slot_tag.family != stint_tag.family

    def test_tag_survives_deactivation(self):
        """Racking is permanent: taking a slot out of service must not free
        the ID printed on the upright."""
        slot = StorageSlotFactory()
        tag_id = ensure_slot_tag(slot).tag_id
        slot.is_active = False
        slot.save()
        assert get_active_tag_id(slot) == tag_id

    def test_pool_exhaustion_degrades_instead_of_blocking(self, monkeypatch):
        # Shrink the family so exhaustion is cheap to reach.
        monkeypatch.setitem(FAMILY_CAPACITY, SLOT_TAG_FAMILY, 1)
        ensure_slot_tag(StorageSlotFactory())  # takes the only ID
        starved = StorageSlotFactory()
        assert ensure_slot_tag(starved) is None
        # The slot still exists and is addressable by code.
        assert StorageSlot.objects.filter(pk=starved.pk).exists()

    def test_detected_tag_resolves_back_to_its_slot(self):
        """The CV 'where is this?' path: tag ID -> slot."""
        slot = StorageSlotFactory(rack=3, level="B", position=2)
        tag_id = ensure_slot_tag(slot).tag_id
        assert resolve_subject(SLOT_TAG_FAMILY, tag_id) == slot


# ---------------------------------------------------------------------------
# Bulk generator (service)
# ---------------------------------------------------------------------------


class TestGenerateRackSlots:
    def test_generates_every_position_of_every_level(self):
        result = generate_rack_slots(
            rack=1,
            levels=[LevelSpec("A", 3), LevelSpec("B", 2)],
        )
        assert [s.code for s in result.created] == ["1A1", "1A2", "1A3", "1B1", "1B2"]
        assert result.skipped == []
        assert StorageSlot.objects.count() == 5

    def test_marks_pallet_jack_per_level(self):
        generate_rack_slots(
            rack=1,
            levels=[
                LevelSpec("A", 2, requires_pallet_jack=False),
                LevelSpec("Y", 2, requires_pallet_jack=True),
            ],
        )
        assert not StorageSlot.objects.get(code="1A1").requires_pallet_jack
        assert StorageSlot.objects.get(code="1Y2").requires_pallet_jack

    def test_allocates_a_tag_per_slot(self):
        result = generate_rack_slots(rack=1, levels=[LevelSpec("A", 3)])
        tag_ids = {get_active_tag_id(slot) for slot in result.created}
        assert None not in tag_ids
        assert len(tag_ids) == 3  # distinct IDs

    def test_rerun_is_idempotent(self):
        first = generate_rack_slots(rack=1, levels=[LevelSpec("A", 3)])
        tags_before = {s.code: get_active_tag_id(s) for s in first.created}

        second = generate_rack_slots(rack=1, levels=[LevelSpec("A", 3)])

        assert second.created == []
        assert [s.code for s in second.skipped] == ["1A1", "1A2", "1A3"]
        assert StorageSlot.objects.count() == 3
        # IDs are stable for life — a re-run must not re-issue them.
        assert {s.code: get_active_tag_id(s) for s in second.skipped} == tags_before

    def test_rerun_extends_a_rack_without_touching_existing_slots(self):
        generate_rack_slots(rack=1, levels=[LevelSpec("A", 2)])
        StorageSlot.objects.filter(code="1A1").update(notes="hand-edited", is_active=False)

        result = generate_rack_slots(
            rack=1,
            levels=[LevelSpec("A", 3), LevelSpec("B", 1)],
            notes="second pass",
        )

        assert [s.code for s in result.created] == ["1A3", "1B1"]
        untouched = StorageSlot.objects.get(code="1A1")
        assert untouched.notes == "hand-edited"
        assert untouched.is_active is False

    def test_rerun_heals_a_slot_that_never_got_a_tag(self, monkeypatch):
        monkeypatch.setitem(FAMILY_CAPACITY, SLOT_TAG_FAMILY, 0)
        starved = generate_rack_slots(rack=1, levels=[LevelSpec("A", 1)])
        assert [s.code for s in starved.without_tag] == ["1A1"]

        monkeypatch.undo()
        healed = generate_rack_slots(rack=1, levels=[LevelSpec("A", 1)])
        assert healed.created == []
        assert healed.without_tag == []
        assert get_active_tag_id(StorageSlot.objects.get(code="1A1")) is not None

    def test_owning_group_is_applied_to_created_slots(self):
        sig = Group.objects.create(name="Woodshop SIG")
        result = generate_rack_slots(rack=4, levels=[LevelSpec("A", 2)], owning_group=sig)
        assert {s.owning_group_id for s in result.created} == {sig.pk}


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class TestSlotApiPermissions:
    def test_anonymous_cannot_list(self):
        assert APIClient().get(SLOTS_URL).status_code in (401, 403)

    def test_anonymous_cannot_generate(self):
        resp = APIClient().post(
            f"{SLOTS_URL}generate/",
            {"rack": 1, "levels": [{"level": "A", "positions": 2}]},
            format="json",
        )
        assert resp.status_code in (401, 403)
        assert StorageSlot.objects.count() == 0

    def test_storage_admin_group_member_may_manage_slots(self):
        User = get_user_model()
        user = User.objects.create_user(username="volunteer", password="x")
        # Seeded by migration 0003 — get_or_create so this doesn't depend on
        # whether the row survived the test DB's fixtures.
        group, _ = Group.objects.get_or_create(name=STORAGE_ADMIN_GROUP)
        user.groups.add(group)
        api = APIClient()
        api.force_authenticate(user=user)

        resp = api.post(SLOTS_URL, {"rack": 1, "level": "A", "position": 1}, format="json")
        assert resp.status_code == 201

    def test_plain_authenticated_user_is_rejected(self):
        User = get_user_model()
        api = APIClient()
        api.force_authenticate(user=User.objects.create_user(username="member", password="x"))
        assert api.get(SLOTS_URL).status_code == 403


class TestSlotApiCrud:
    def test_create_computes_the_code_and_allocates_a_tag(self, staff_client):
        resp = staff_client.post(
            SLOTS_URL,
            {"rack": 2, "level": "b", "position": 3, "requires_pallet_jack": False},
            format="json",
        )
        assert resp.status_code == 201, resp.data
        assert resp.data["code"] == "2B3"
        assert resp.data["level"] == "B"
        assert resp.data["april_tag_id"] is not None

    def test_create_ignores_a_client_supplied_code(self, staff_client):
        resp = staff_client.post(
            SLOTS_URL,
            {"rack": 1, "level": "A", "position": 1, "code": "9Z9"},
            format="json",
        )
        assert resp.status_code == 201
        assert resp.data["code"] == "1A1"

    def test_duplicate_slot_is_a_400_not_a_500(self, staff_client):
        StorageSlotFactory(rack=1, level="A", position=1)
        resp = staff_client.post(
            SLOTS_URL,
            {"rack": 1, "level": "a", "position": 1},
            format="json",
        )
        assert resp.status_code == 400

    def test_retrieve_by_code(self, staff_client):
        slot = StorageSlotFactory(rack=1, level="A", position=1)
        ensure_slot_tag(slot)
        resp = staff_client.get(f"{SLOTS_URL}1A1/")
        assert resp.status_code == 200
        assert resp.data["code"] == "1A1"
        assert resp.data["april_tag_id"] == 0

    def test_unowned_slot_still_reports_every_field(self, staff_client):
        """A null owning_group must not make DRF drop the name key."""
        StorageSlotFactory(rack=1, level="A", position=1)
        resp = staff_client.get(f"{SLOTS_URL}1A1/")
        assert resp.data["owning_group"] is None
        assert resp.data["owning_group_name"] == ""

    def test_patch_recomputes_the_code(self, staff_client):
        StorageSlotFactory(rack=1, level="A", position=1)
        resp = staff_client.patch(f"{SLOTS_URL}1A1/", {"position": 5}, format="json")
        assert resp.status_code == 200
        assert resp.data["code"] == "1A5"

    def test_patch_can_deactivate_without_losing_the_tag(self, staff_client):
        slot = StorageSlotFactory(rack=1, level="A", position=1)
        tag_id = ensure_slot_tag(slot).tag_id
        resp = staff_client.patch(f"{SLOTS_URL}1A1/", {"is_active": False}, format="json")
        assert resp.status_code == 200
        assert resp.data["is_active"] is False
        assert resp.data["april_tag_id"] == tag_id

    def test_delete_releases_the_tag(self, staff_client):
        """The one case where a slot's ID goes back to the pool — otherwise
        the assignment would dangle on a deleted row."""
        slot = StorageSlotFactory(rack=1, level="A", position=1)
        tag_id = ensure_slot_tag(slot).tag_id

        assert staff_client.delete(f"{SLOTS_URL}1A1/").status_code == 204

        assert not StorageSlot.objects.filter(code="1A1").exists()
        assert resolve_subject(SLOT_TAG_FAMILY, tag_id) is None
        assert not AprilTagAssignment.objects.filter(
            family=SLOT_TAG_FAMILY, tag_id=tag_id, released_at__isnull=True
        ).exists()

    def test_owning_group_round_trips_with_its_name(self, staff_client):
        sig = Group.objects.create(name="Metal SIG")
        resp = staff_client.post(
            SLOTS_URL,
            {"rack": 1, "level": "A", "position": 1, "owning_group": sig.pk},
            format="json",
        )
        assert resp.status_code == 201
        assert resp.data["owning_group"] == sig.pk
        assert resp.data["owning_group_name"] == "Metal SIG"


class TestSlotApiFilters:
    @pytest.fixture(autouse=True)
    def _slots(self):
        generate_rack_slots(
            rack=1,
            levels=[LevelSpec("A", 2), LevelSpec("Y", 2, requires_pallet_jack=True)],
        )
        generate_rack_slots(rack=2, levels=[LevelSpec("A", 1)])
        StorageSlot.objects.filter(code="1A2").update(is_active=False)

    def _codes(self, resp):
        rows = resp.data["results"] if isinstance(resp.data, dict) else resp.data
        return [row["code"] for row in rows]

    def test_filter_by_rack(self, staff_client):
        assert self._codes(staff_client.get(f"{SLOTS_URL}?rack=2")) == ["2A1"]

    def test_filter_by_level_is_case_insensitive(self, staff_client):
        assert self._codes(staff_client.get(f"{SLOTS_URL}?rack=1&level=y")) == ["1Y1", "1Y2"]

    def test_filter_by_is_active_false(self, staff_client):
        assert self._codes(staff_client.get(f"{SLOTS_URL}?is_active=false")) == ["1A2"]

    def test_filter_by_requires_pallet_jack(self, staff_client):
        assert self._codes(staff_client.get(f"{SLOTS_URL}?requires_pallet_jack=true")) == [
            "1Y1",
            "1Y2",
        ]

    def test_junk_rack_returns_nothing_rather_than_erroring(self, staff_client):
        resp = staff_client.get(f"{SLOTS_URL}?rack=not-a-number")
        assert resp.status_code == 200
        assert self._codes(resp) == []

    def test_list_does_not_query_per_row_for_the_tag(
        self, staff_client, django_assert_max_num_queries
    ):
        with django_assert_max_num_queries(6):
            resp = staff_client.get(SLOTS_URL)
        assert len(self._codes(resp)) == 5


class TestGenerateEndpoint:
    def test_creates_the_rack_and_reports_created(self, staff_client):
        resp = staff_client.post(
            f"{SLOTS_URL}generate/",
            {
                "rack": 1,
                "levels": [
                    {"level": "A", "positions": 3},
                    {"level": "Y", "positions": 2, "requires_pallet_jack": True},
                ],
            },
            format="json",
        )
        assert resp.status_code == 201, resp.data
        assert resp.data["created"] == ["1A1", "1A2", "1A3", "1Y1", "1Y2"]
        assert resp.data["skipped"] == []
        assert resp.data["created_count"] == 5
        assert [s["code"] for s in resp.data["slots"]] == resp.data["created"]
        assert all(s["april_tag_id"] is not None for s in resp.data["slots"])
        assert [s["requires_pallet_jack"] for s in resp.data["slots"]] == [
            False,
            False,
            False,
            True,
            True,
        ]

    def test_rerun_reports_everything_skipped(self, staff_client):
        payload = {"rack": 1, "levels": [{"level": "A", "positions": 2}]}
        staff_client.post(f"{SLOTS_URL}generate/", payload, format="json")
        resp = staff_client.post(f"{SLOTS_URL}generate/", payload, format="json")

        assert resp.status_code == 200
        assert resp.data["created"] == []
        assert resp.data["skipped"] == ["1A1", "1A2"]
        assert StorageSlot.objects.count() == 2

    def test_accepts_an_owning_group(self, staff_client):
        sig = Group.objects.create(name="Textiles SIG")
        resp = staff_client.post(
            f"{SLOTS_URL}generate/",
            {"rack": 7, "levels": [{"level": "A", "positions": 1}], "owning_group": sig.pk},
            format="json",
        )
        assert resp.status_code == 201
        assert StorageSlot.objects.get(code="7A1").owning_group_id == sig.pk

    @pytest.mark.parametrize(
        "payload",
        [
            {"rack": 0, "levels": [{"level": "A", "positions": 1}]},
            {"rack": 1, "levels": []},
            {"rack": 1, "levels": [{"level": "AB", "positions": 1}]},
            {"rack": 1, "levels": [{"level": "1", "positions": 1}]},
            {"rack": 1, "levels": [{"level": "A", "positions": 0}]},
            {"rack": 1, "levels": [{"level": "A", "positions": 1000}]},
            {"levels": [{"level": "A", "positions": 1}]},
        ],
    )
    def test_rejects_bad_specs(self, staff_client, payload):
        resp = staff_client.post(f"{SLOTS_URL}generate/", payload, format="json")
        assert resp.status_code == 400, resp.data
        assert StorageSlot.objects.count() == 0

    def test_rejects_a_repeated_level(self, staff_client):
        resp = staff_client.post(
            f"{SLOTS_URL}generate/",
            {
                "rack": 1,
                "levels": [
                    {"level": "A", "positions": 1},
                    {"level": "a", "positions": 2},
                ],
            },
            format="json",
        )
        assert resp.status_code == 400
        assert StorageSlot.objects.count() == 0


# ---------------------------------------------------------------------------
# Management command
# ---------------------------------------------------------------------------


class TestGenerateStorageSlotsCommand:
    def test_generates_a_rack(self):
        call_command(
            "generate_storage_slots", "--rack", "1", "--level", "A:2", "--level", "Y:1:jack"
        )
        assert [s.code for s in StorageSlot.objects.all()] == ["1A1", "1A2", "1Y1"]
        assert StorageSlot.objects.get(code="1Y1").requires_pallet_jack
        assert get_active_tag_id(StorageSlot.objects.get(code="1A1")) is not None

    def test_rerun_is_idempotent(self):
        for _ in range(2):
            call_command("generate_storage_slots", "--rack", "1", "--level", "A:2")
        assert StorageSlot.objects.count() == 2

    @pytest.mark.parametrize("level", ["A", "A:0", "AB:2", "A:2:nope", "A:2:3:4"])
    def test_rejects_bad_level_specs(self, level):
        with pytest.raises(CommandError):
            call_command("generate_storage_slots", "--rack", "1", "--level", level)

    def test_rejects_a_repeated_level(self):
        with pytest.raises(CommandError):
            call_command(
                "generate_storage_slots", "--rack", "1", "--level", "A:1", "--level", "a:2"
            )


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


def test_admin_created_slot_gets_a_tag():
    """The admin is a first-class creation path, not a back door that
    silently skips the marker."""
    from django.contrib.admin.sites import AdminSite

    from project_storage.admin import StorageSlotAdmin

    admin = StorageSlotAdmin(StorageSlot, AdminSite())
    slot = StorageSlot(rack=5, level="A", position=1)
    admin.save_model(request=None, obj=slot, form=None, change=False)

    assert get_active_tag_id(slot) is not None
    assert admin.april_tag_id(slot) == "0"
