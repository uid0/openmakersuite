"""Tests for ``auto_generate_bin_id`` (PR1).

The allocator returns the next unused ``MBX-NNN`` slot by reading the
highest existing bin_id with the same prefix. The DB-level partial
unique constraint is the source of truth for collision rejection —
this just covers the happy-path numbering, prefix isolation, and
robustness to pre-conversion (null) rows.
"""

from __future__ import annotations

import pytest

from maker_boxes.models import MakerBox, auto_generate_bin_id

pytestmark = pytest.mark.django_db


def test_first_allocation_returns_001():
    assert auto_generate_bin_id() == "MBX-001"


def test_sequential_allocation_increments():
    MakerBox.objects.create(bin_id="MBX-001")
    assert auto_generate_bin_id() == "MBX-002"

    MakerBox.objects.create(bin_id="MBX-002")
    assert auto_generate_bin_id() == "MBX-003"


def test_skips_gaps_and_picks_after_highest():
    # The allocator picks max+1, not min-of-gaps. Reusing freed numbers
    # would confuse audit trails — once a bin is destroyed, its number
    # is gone.
    MakerBox.objects.create(bin_id="MBX-001")
    MakerBox.objects.create(bin_id="MBX-005")
    assert auto_generate_bin_id() == "MBX-006"


def test_ignores_other_prefixes():
    # Legacy PSB-* rows must not be counted by an MBX allocation.
    MakerBox.objects.create(bin_id="PSB-099")
    assert auto_generate_bin_id() == "MBX-001"


def test_ignores_pre_conversion_rows_with_null_bin_id():
    # bin_id is nullable for pre-conversion queue rows.
    MakerBox.objects.create(
        bin_id=None,
        assigned_username="ada",
        status=MakerBox.STATUS_PRE_CONVERSION,
    )
    MakerBox.objects.create(bin_id="MBX-007")
    assert auto_generate_bin_id() == "MBX-008"


def test_handles_malformed_suffix_without_crashing():
    # If somehow a row got "MBX-foo" in it (manual admin entry, broken
    # migration), the allocator should not raise — it falls back to
    # treating the malformed row as if it were 0.
    MakerBox.objects.create(bin_id="MBX-foo")
    # "MBX-foo" sorts higher than "MBX-001" lexicographically, so the
    # query returns it first; the suffix parse fails and we fall back
    # to the default next_num = 1.
    assert auto_generate_bin_id() == "MBX-001"


def test_respects_custom_prefix_and_padding():
    MakerBox.objects.create(bin_id="MBX-042")
    assert auto_generate_bin_id(prefix="PSB", padding=4) == "PSB-0001"


def test_partial_unique_allows_multiple_null_bin_ids():
    # Pre-conversion rows are intentionally null on bin_id, and we want
    # many of them coexisting (the queue). The Meta.constraints partial
    # unique index should not reject these.
    a = MakerBox.objects.create(
        bin_id=None,
        assigned_username="ada",
        status=MakerBox.STATUS_PRE_CONVERSION,
    )
    b = MakerBox.objects.create(
        bin_id=None,
        assigned_username="bob",
        status=MakerBox.STATUS_PRE_CONVERSION,
    )
    assert a.pk != b.pk
    assert MakerBox.objects.filter(bin_id__isnull=True).count() == 2


def test_partial_unique_rejects_duplicate_non_null_bin_id():
    from django.db import IntegrityError, transaction

    MakerBox.objects.create(bin_id="MBX-001")
    with pytest.raises(IntegrityError), transaction.atomic():
        MakerBox.objects.create(bin_id="MBX-001")
