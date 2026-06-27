"""Tests for the global AprilTag allocator (op-e9w).

Covers the lowest-free pick, reuse-after-release, per-subject idempotency,
the global pool shared across record types, pool exhaustion, and the
partial-unique-when-active constraints that back concurrency.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction

import pytest

from fiducials.models import (
    FAMILY_CAPACITY,
    FAMILY_TAG36H10,
    FAMILY_TAG36H11,
    AprilTagAssignment,
)
from fiducials.services.allocator import (
    AprilTagPoolExhausted,
    active_tag_id_subquery,
    allocate_tag,
    get_active_assignment,
    get_active_tag_id,
    release_tag,
)

User = get_user_model()
pytestmark = pytest.mark.django_db


def _subject(n: int):
    """A throwaway saved record to hang a tag on (any model works)."""
    return User.objects.create_user(username=f"u{n}", password="x")


def test_first_allocation_is_id_zero():
    a = allocate_tag(_subject(1))
    assert a.tag_id == 0
    assert a.family == FAMILY_TAG36H11
    assert a.released_at is None


def test_allocator_picks_lowest_free_id():
    allocate_tag(_subject(1))  # 0
    allocate_tag(_subject(2))  # 1
    assert allocate_tag(_subject(3)).tag_id == 2


def test_allocation_is_idempotent_per_subject():
    u = _subject(1)
    first = allocate_tag(u)
    again = allocate_tag(u)
    assert first.pk == again.pk
    assert AprilTagAssignment.objects.filter(object_id=u.pk, released_at__isnull=True).count() == 1


def test_release_frees_lowest_id_for_reuse():
    u1 = _subject(1)
    assert allocate_tag(u1).tag_id == 0
    released = release_tag(u1)
    assert released is not None and released.released_at is not None
    # The freed id 0 is the lowest free again, so the next subject reuses it.
    u2 = _subject(2)
    assert allocate_tag(u2).tag_id == 0
    assert get_active_tag_id(u1) is None
    assert get_active_tag_id(u2) == 0


def test_release_is_idempotent():
    u = _subject(1)
    allocate_tag(u)
    assert release_tag(u) is not None
    assert release_tag(u) is None  # already released -> no-op


def test_release_with_no_active_assignment_is_noop():
    assert release_tag(_subject(1)) is None


def test_pool_is_global_across_record_types():
    # A User and a Group draw from the SAME pool, so their ids differ.
    user_tag = allocate_tag(_subject(1))
    group_tag = allocate_tag(Group.objects.create(name="g1"))
    assert user_tag.tag_id != group_tag.tag_id
    assert {user_tag.tag_id, group_tag.tag_id} == {0, 1}


def test_pool_exhausted_raises(monkeypatch):
    # Shrink the family so exhaustion is cheap to reach.
    monkeypatch.setitem(FAMILY_CAPACITY, FAMILY_TAG36H11, 2)
    allocate_tag(_subject(1))  # 0
    allocate_tag(_subject(2))  # 1
    with pytest.raises(AprilTagPoolExhausted):
        allocate_tag(_subject(3))


def test_released_id_is_reusable_after_exhaustion(monkeypatch):
    monkeypatch.setitem(FAMILY_CAPACITY, FAMILY_TAG36H11, 1)
    u1 = _subject(1)
    allocate_tag(u1)  # 0, pool now full
    with pytest.raises(AprilTagPoolExhausted):
        allocate_tag(_subject(2))
    release_tag(u1)  # free id 0
    assert allocate_tag(_subject(3)).tag_id == 0


def test_allocate_rejects_unsaved_subject():
    with pytest.raises(ValueError):
        allocate_tag(User(username="ghost"))  # never saved -> pk is None


def test_allocate_rejects_unknown_family():
    with pytest.raises(ValueError):
        allocate_tag(_subject(1), family="tagBogus")


def test_partial_unique_rejects_two_active_same_family_id():
    ct = ContentType.objects.get_for_model(User)
    u1, u2 = _subject(1), _subject(2)
    AprilTagAssignment.objects.create(
        family=FAMILY_TAG36H11, tag_id=5, content_type=ct, object_id=u1.pk
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        AprilTagAssignment.objects.create(
            family=FAMILY_TAG36H11, tag_id=5, content_type=ct, object_id=u2.pk
        )


def test_partial_unique_allows_same_id_after_release():
    ct = ContentType.objects.get_for_model(User)
    u1, u2 = _subject(1), _subject(2)
    first = AprilTagAssignment.objects.create(
        family=FAMILY_TAG36H11, tag_id=5, content_type=ct, object_id=u1.pk
    )
    release_tag(u1)
    # Now that #5 is released, a second active #5 is allowed.
    second = AprilTagAssignment.objects.create(
        family=FAMILY_TAG36H11, tag_id=5, content_type=ct, object_id=u2.pk
    )
    assert first.pk != second.pk


def test_partial_unique_rejects_two_active_tags_for_one_subject():
    # The per-subject index is family-agnostic: a record holds at most one
    # active tag across all families.
    ct = ContentType.objects.get_for_model(User)
    u = _subject(1)
    AprilTagAssignment.objects.create(
        family=FAMILY_TAG36H11, tag_id=0, content_type=ct, object_id=u.pk
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        AprilTagAssignment.objects.create(
            family=FAMILY_TAG36H10, tag_id=0, content_type=ct, object_id=u.pk
        )


def test_active_tag_id_subquery_annotates_without_query_per_row(django_assert_num_queries):
    u1, u2 = _subject(1), _subject(2)
    allocate_tag(u1)  # id 0
    # One query for the whole annotated queryset (no N+1).
    with django_assert_num_queries(1):
        rows = {
            row.pk: row.active_april_tag_id
            for row in User.objects.annotate(active_april_tag_id=active_tag_id_subquery(User))
        }
    assert rows[u1.pk] == 0
    assert rows[u2.pk] is None


def test_get_active_assignment_returns_none_for_unsaved():
    assert get_active_assignment(User(username="ghost")) is None
