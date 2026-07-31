"""The storage overview — one glanceable status grid per rack.

The job this exists for: standing anywhere with a phone and seeing which
slots are wrong. So the payload is shaped like the rack itself rather than
like the tables behind it — a row per level with the high levels first (you
read a rack top-down), a dense array of positions per row, and one flat cell
per slot carrying everything a renderer needs.

A cell says four things:

``type``
    ``P`` (a member's project), ``C`` / ``L`` / ``E`` (a committee,
    logistics, or class holding), or ``None`` for an empty slot.
``status``
    for ``P``, the stint's own :meth:`~project_storage.models.
    ProjectStorageStint.compute_status`; ``"occupied"`` for the long-term
    types, which have no clock to be late against; ``"empty"`` otherwise.
``color``
    what to paint it, and **only Project storage is ever painted**. A
    committee slot has been the committee's for two years and will be
    tomorrow — colouring it would mean the grid is mostly coloured and the
    one expired member project stops standing out, which is the entire
    point of looking at it.
``occupant``
    who is in it, for the ScanTTY table and the web tooltip.

``cells`` is dense and 1-indexed by position: entry *i* is position *i + 1*,
and a hole in the racking (no slot at that position) is ``None``. Both the
web grid and the ASCII table want to lay out a fixed-width row without
re-deriving which columns exist, so the gaps are in the payload.
"""

from __future__ import annotations

from typing import Optional

from django.utils import timezone

from ..models import ProjectStorageStint, StorageSlot

#: A slot nobody is in.
STATUS_EMPTY = "empty"
#: A committee/logistics/class holding. Long-term by design, so unlike a
#: stint it has no expiry state to report — it is simply in use.
STATUS_OCCUPIED = "occupied"

COLOR_YELLOW = "yellow"
COLOR_RED = "red"

#: The one definition of "which Project states are worth a colour". Anything
#: absent from this map (an active, in-date stint) paints nothing, so a
#: healthy rack is a wall of plain cells and the exceptions jump out.
PROJECT_STATUS_COLORS = {
    ProjectStorageStint.STATUS_EXPIRING_SOON: COLOR_YELLOW,
    ProjectStorageStint.STATUS_EXPIRED: COLOR_RED,
    ProjectStorageStint.STATUS_PURGATORY_WARNED: COLOR_RED,
    # A purgatoried stint no longer counts as occupying its slot, so this
    # can't normally be reached through current_occupancy. Mapped anyway:
    # the colour rule is a contract about statuses, not about which ones
    # today's occupancy predicate happens to admit.
    ProjectStorageStint.STATUS_PURGATORY: COLOR_RED,
}


def _coerce_rack(rack) -> Optional[int]:
    """Rack filters arrive as query strings. Junk matches nothing.

    Mirrors ``StorageSlotViewSet.get_queryset`` — a fat-fingered ``?rack=x``
    returns an empty grid rather than a 500.
    """
    if rack is None or rack == "":
        return None
    try:
        return int(rack)
    except (TypeError, ValueError):
        return -1  # No rack is numbered -1, so this matches nothing.


def cell_for_slot(slot: StorageSlot, *, now=None) -> dict:
    """One grid cell for one slot.

    Reads occupancy through :attr:`StorageSlot.current_occupancy`, so the
    "stint or assignment, never both" tie-break has a single home. Callers
    are expected to have set up both occupancy prefetches (see
    :func:`build_overview`); without them this costs two queries per slot.
    """
    occupancy = slot.current_occupancy

    if occupancy is None:
        type_letter, status, color, occupant = None, STATUS_EMPTY, None, ""
    elif isinstance(occupancy, ProjectStorageStint):
        status = occupancy.compute_status(now)
        type_letter = occupancy.type_letter
        color = PROJECT_STATUS_COLORS.get(status)
        occupant = occupancy.display_name
    else:
        type_letter = occupancy.type_letter
        status = STATUS_OCCUPIED
        color = None
        occupant = occupancy.occupant_display

    return {
        "code": slot.code,
        "slot_id": slot.pk,
        "position": slot.position,
        "type": type_letter,
        "status": status,
        "color": color,
        "occupant": occupant,
        # The slot's own in-service flag — a retired slot is empty but is not
        # available, and a grid that can't tell those apart invites the
        # warden to hand out a slot that isn't there any more.
        "is_active": slot.is_active,
    }


def build_overview(*, rack=None, now=None) -> dict:
    """Build the whole overview payload. Three queries, whatever the rack size.

    ``rack`` optionally narrows to one rack (an int or a query-string digit).
    """
    now = now or timezone.now()

    slots = (
        StorageSlot.objects.all()
        .prefetch_related(
            # Both occupancy sides in one shot. Without these,
            # current_occupancy is two queries per slot and a 200-slot aisle
            # is 400 round trips for a screen somebody refreshes on a phone.
            StorageSlot.active_stints_prefetch(),
            StorageSlot.active_assignments_prefetch(),
        )
        .order_by("rack", "level", "position")
    )
    rack_filter = _coerce_rack(rack)
    if rack_filter is not None:
        slots = slots.filter(rack=rack_filter)

    by_rack: dict[int, list[StorageSlot]] = {}
    for slot in slots:
        by_rack.setdefault(slot.rack, []).append(slot)

    racks = []
    for rack_number, rack_slots in sorted(by_rack.items()):
        # Descending, so the top of the payload is the top of the rack: Z is
        # overhead and A is at your feet, and the grid reads the way the
        # steel does.
        levels = sorted({slot.level for slot in rack_slots}, reverse=True)
        # Grid width. Positions are 1-based and may be sparse (a rack can
        # have 12 slots on A and 8 on C), so every row is padded to the
        # widest level rather than to its own length.
        max_position = max(slot.position for slot in rack_slots)

        cells: dict[str, dict[int, dict]] = {}
        for slot in rack_slots:
            cells.setdefault(slot.level, {})[slot.position] = cell_for_slot(slot, now=now)

        racks.append(
            {
                "rack": rack_number,
                "levels": levels,
                "max_position": max_position,
                "rows": [
                    {
                        "level": level,
                        "cells": [
                            cells[level].get(position) for position in range(1, max_position + 1)
                        ],
                    }
                    for level in levels
                ],
            }
        )

    return {"racks": racks, "generated_at": now}
