"""The per-rack overview grid (op-wgc8, backend PR6).

The read this exists for: pull the rack up on a phone, look for the coloured
cells, go deal with those. So the tests pin the things that make that read
work — the grid is laid out like the steel (high levels first, one column per
position, holes preserved), every storage type paints its own letter, and
*only* Project storage is ever coloured, because a grid where the committee
slots are lit up too is a grid nobody can scan.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from project_storage.models import ProjectStorageStint, StorageAssignment
from project_storage.permissions import STORAGE_ADMIN_GROUP
from project_storage.services.storage_overview import PROJECT_STATUS_COLORS
from project_storage.tests.factories import (
    ProjectStorageStintFactory,
    StorageAssignmentFactory,
    StorageSlotFactory,
)

pytestmark = pytest.mark.django_db

OVERVIEW_URL = "/api/project-storage/overview/"


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


def _stint_at(slot, *, expires_in_days=None, **extra):
    """A live stint in ``slot``, expiring however far out the caller wants."""
    now = timezone.now()
    if expires_in_days is not None:
        extra["expires_at"] = now + timedelta(days=expires_in_days)
    return ProjectStorageStintFactory(slot=slot, **extra)


def _cells(body, rack=0):
    """{code: cell} for one rack, ignoring the grid holes."""
    return {
        cell["code"]: cell
        for row in body["racks"][rack]["rows"]
        for cell in row["cells"]
        if cell is not None
    }


# ---------------------------------------------------------------------------
# Grid layout
# ---------------------------------------------------------------------------


class TestGridLayout:
    def test_levels_are_listed_high_to_low(self, staff_client):
        for level in ("A", "C", "B"):
            StorageSlotFactory(rack=1, level=level, position=1)

        rack = staff_client.get(OVERVIEW_URL).json()["racks"][0]

        # Z overhead, A at your feet — the payload reads the way the rack does.
        assert rack["levels"] == ["C", "B", "A"]
        assert [row["level"] for row in rack["rows"]] == ["C", "B", "A"]

    def test_grid_width_is_the_widest_level(self, staff_client):
        for position in range(1, 5):
            StorageSlotFactory(rack=1, level="A", position=position)
        StorageSlotFactory(rack=1, level="B", position=1)

        rack = staff_client.get(OVERVIEW_URL).json()["racks"][0]

        assert rack["max_position"] == 4
        # Every row is padded to the rack's width so the columns line up.
        assert all(len(row["cells"]) == 4 for row in rack["rows"])

    def test_missing_positions_are_holes_not_shifts(self, staff_client):
        StorageSlotFactory(rack=1, level="A", position=1)
        StorageSlotFactory(rack=1, level="A", position=3)

        cells = staff_client.get(OVERVIEW_URL).json()["racks"][0]["rows"][0]["cells"]

        # 1-indexed and dense: position 2 is a hole, not a closed gap.
        assert [cell and cell["code"] for cell in cells] == ["1A1", None, "1A3"]

    def test_racks_come_back_in_numeric_order(self, staff_client):
        StorageSlotFactory(rack=2, level="A", position=1)
        StorageSlotFactory(rack=1, level="A", position=1)
        StorageSlotFactory(rack=10, level="A", position=1)

        body = staff_client.get(OVERVIEW_URL).json()

        assert [rack["rack"] for rack in body["racks"]] == [1, 2, 10]

    def test_rack_filter_narrows_to_one_rack(self, staff_client):
        StorageSlotFactory(rack=1, level="A", position=1)
        StorageSlotFactory(rack=2, level="A", position=1)

        body = staff_client.get(f"{OVERVIEW_URL}?rack=2").json()

        assert [rack["rack"] for rack in body["racks"]] == [2]

    def test_junk_rack_filter_matches_nothing(self, staff_client):
        StorageSlotFactory(rack=1, level="A", position=1)

        assert staff_client.get(f"{OVERVIEW_URL}?rack=nope").json()["racks"] == []

    def test_empty_racking_is_an_empty_grid(self, staff_client):
        body = staff_client.get(OVERVIEW_URL).json()

        assert body["racks"] == []
        assert body["generated_at"]


# ---------------------------------------------------------------------------
# What a cell says
# ---------------------------------------------------------------------------


class TestCellTypes:
    def test_empty_slot(self, staff_client):
        StorageSlotFactory(rack=1, level="A", position=1)

        cell = _cells(staff_client.get(OVERVIEW_URL).json())["1A1"]

        assert cell["type"] is None
        assert cell["status"] == "empty"
        assert cell["color"] is None
        assert cell["occupant"] == ""
        assert cell["is_active"] is True

    def test_project_stint_is_p(self, staff_client):
        slot = StorageSlotFactory(rack=1, level="A", position=1)
        _stint_at(slot, username="ada", first_name="Ada", last_name="Byron")

        cell = _cells(staff_client.get(OVERVIEW_URL).json())["1A1"]

        assert cell["type"] == "P"
        assert cell["occupant"] == "Ada Byron"

    def test_committee_assignment_is_c(self, staff_client):
        slot = StorageSlotFactory(rack=1, level="A", position=1)
        StorageAssignmentFactory(
            slot=slot,
            storage_type=StorageAssignment.TYPE_COMMITTEE,
            owning_group=Group.objects.create(name="Welding SIG"),
        )

        cell = _cells(staff_client.get(OVERVIEW_URL).json())["1A1"]

        assert cell["type"] == "C"
        assert cell["status"] == "occupied"
        assert cell["occupant"] == "Welding SIG"

    def test_logistics_assignment_is_l(self, staff_client):
        slot = StorageSlotFactory(rack=1, level="A", position=1)
        StorageAssignmentFactory(
            slot=slot,
            storage_type=StorageAssignment.TYPE_LOGISTICS,
            occupant_label="Dock crew",
        )

        cell = _cells(staff_client.get(OVERVIEW_URL).json())["1A1"]

        assert cell["type"] == "L"
        assert cell["status"] == "occupied"
        assert cell["occupant"] == "Dock crew"

    def test_class_assignment_is_e(self, staff_client):
        slot = StorageSlotFactory(rack=1, level="A", position=1)
        StorageAssignmentFactory(
            slot=slot,
            storage_type=StorageAssignment.TYPE_CLASS,
            occupant_label="Ana's CNC class",
        )

        cell = _cells(staff_client.get(OVERVIEW_URL).json())["1A1"]

        assert cell["type"] == "E"
        assert cell["status"] == "occupied"
        assert cell["occupant"] == "Ana's CNC class"

    def test_released_assignment_leaves_the_slot_empty(self, staff_client):
        slot = StorageSlotFactory(rack=1, level="A", position=1)
        StorageAssignmentFactory(slot=slot, released_at=timezone.now())

        cell = _cells(staff_client.get(OVERVIEW_URL).json())["1A1"]

        assert cell["type"] is None
        assert cell["status"] == "empty"

    def test_resolved_stint_leaves_the_slot_empty(self, staff_client):
        slot = StorageSlotFactory(rack=1, level="A", position=1)
        _stint_at(slot, username="ada", removed_at=timezone.now())

        assert _cells(staff_client.get(OVERVIEW_URL).json())["1A1"]["type"] is None

    def test_a_stint_wins_a_double_occupancy(self, staff_client):
        """Nothing at the DB can stop both rows existing (they're in different
        tables). The member's clock is the one that has to keep being watched,
        so it wins the cell."""
        slot = StorageSlotFactory(rack=1, level="A", position=1)
        StorageAssignmentFactory(slot=slot, occupant_label="Dock crew")
        _stint_at(slot, username="ada")

        assert _cells(staff_client.get(OVERVIEW_URL).json())["1A1"]["type"] == "P"

    def test_retired_slot_is_flagged(self, staff_client):
        StorageSlotFactory(rack=1, level="A", position=1, is_active=False)

        cell = _cells(staff_client.get(OVERVIEW_URL).json())["1A1"]

        # Still in the grid — the card is still on the upright — but not
        # available, and the grid has to be able to say so.
        assert cell["is_active"] is False
        assert cell["status"] == "empty"

    def test_cell_carries_its_slot_id_and_position(self, staff_client):
        slot = StorageSlotFactory(rack=1, level="A", position=3)

        cell = _cells(staff_client.get(OVERVIEW_URL).json())["1A3"]

        assert cell["slot_id"] == slot.pk
        assert cell["position"] == 3


# ---------------------------------------------------------------------------
# Colour — the whole point of the screen
# ---------------------------------------------------------------------------


class TestProjectColors:
    @pytest.mark.parametrize(
        "expires_in_days,expected_status,expected_color",
        [
            (30, ProjectStorageStint.STATUS_ACTIVE, None),
            (1, ProjectStorageStint.STATUS_EXPIRING_SOON, "yellow"),
            (-1, ProjectStorageStint.STATUS_EXPIRED, "red"),
        ],
    )
    def test_project_status_drives_the_color(
        self, staff_client, expires_in_days, expected_status, expected_color
    ):
        slot = StorageSlotFactory(rack=1, level="A", position=1)
        _stint_at(slot, username="ada", expires_in_days=expires_in_days)

        cell = _cells(staff_client.get(OVERVIEW_URL).json())["1A1"]

        assert cell["type"] == "P"
        assert cell["status"] == expected_status
        assert cell["color"] == expected_color

    def test_warned_stint_is_red(self, staff_client):
        slot = StorageSlotFactory(rack=1, level="A", position=1)
        _stint_at(slot, username="ada", expires_in_days=-8, notice_sent_at=timezone.now())

        cell = _cells(staff_client.get(OVERVIEW_URL).json())["1A1"]

        assert cell["status"] == ProjectStorageStint.STATUS_PURGATORY_WARNED
        assert cell["color"] == "red"

    def test_assignments_are_never_colored(self, staff_client):
        """A committee has held its slot for two years and will tomorrow.
        Colouring that would drown the one expired member project."""
        for position, storage_type in enumerate(
            (
                StorageAssignment.TYPE_COMMITTEE,
                StorageAssignment.TYPE_LOGISTICS,
                StorageAssignment.TYPE_CLASS,
            ),
            start=1,
        ):
            StorageAssignmentFactory(
                slot=StorageSlotFactory(rack=1, level="A", position=position),
                storage_type=storage_type,
                occupant_label="Somebody",
                # Older than any stint would be allowed to get.
                assigned_at=timezone.now() - timedelta(days=900),
            )

        cells = _cells(staff_client.get(OVERVIEW_URL).json())

        assert {cell["color"] for cell in cells.values()} == {None}
        assert {cell["status"] for cell in cells.values()} == {"occupied"}

    def test_every_alarming_project_status_has_a_color(self):
        # The colour map is the contract; if a new "something is wrong"
        # status appears on a stint it has to be added here deliberately.
        assert set(PROJECT_STATUS_COLORS) == {
            ProjectStorageStint.STATUS_EXPIRING_SOON,
            ProjectStorageStint.STATUS_EXPIRED,
            ProjectStorageStint.STATUS_PURGATORY_WARNED,
            ProjectStorageStint.STATUS_PURGATORY,
        }
        assert set(PROJECT_STATUS_COLORS.values()) == {"yellow", "red"}


# ---------------------------------------------------------------------------
# Access + cost
# ---------------------------------------------------------------------------


class TestOverviewAccess:
    def test_anonymous_is_rejected(self, client):
        assert client.get(OVERVIEW_URL).status_code in (401, 403)

    def test_storage_admin_group_can_read_it(self, client):
        User = get_user_model()
        user = User.objects.create_user(username="volunteer", password="x")
        # The group ships with the app (migration 0003) — get_or_create, not
        # create, or this collides with it.
        group, _ = Group.objects.get_or_create(name=STORAGE_ADMIN_GROUP)
        user.groups.add(group)
        client.force_authenticate(user=user)

        assert client.get(OVERVIEW_URL).status_code == 200


class TestOverviewCost:
    def test_a_whole_aisle_costs_a_fixed_number_of_queries(
        self, staff_client, django_assert_max_num_queries
    ):
        """Ian refreshes this on a phone. It has to be flat in the slot count,
        not one query per cell."""
        for level in ("A", "B", "C"):
            for position in range(1, 11):
                slot = StorageSlotFactory(rack=1, level=level, position=position)
                if position % 3 == 0:
                    _stint_at(slot, username=f"m{level}{position}")
                elif position % 3 == 1:
                    StorageAssignmentFactory(
                        slot=slot,
                        storage_type=StorageAssignment.TYPE_COMMITTEE,
                        owning_group=Group.objects.create(name=f"SIG {level}{position}"),
                    )

        # 30 slots, 20 of them occupied: the slot query plus one prefetch per
        # occupancy kind, and nothing that scales with the rack.
        with django_assert_max_num_queries(6):
            body = staff_client.get(OVERVIEW_URL).json()

        assert len(_cells(body)) == 30
