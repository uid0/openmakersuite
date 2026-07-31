"""Allocate / release AprilTag IDs from the global pool.

Public API:

* :func:`allocate_tag` — idempotently hand a subject record the lowest free
  AprilTag ID in a family. Raises :class:`AprilTagPoolExhausted` when the
  family has no free IDs left (the finite-pool failure mode fails *loud* —
  callers decide whether to block or degrade; see the lifecycle hooks in
  project_storage / maker_boxes).
* :func:`release_tag` — retire a subject's active allocation so its ID
  recycles.
* :func:`get_active_assignment` / :func:`get_active_tag_id` — read-side
  lookup used by the label renderers to decide whether (and which) tag to
  draw.
* :func:`resolve_subject` — the reverse direction: a camera detected tag
  ``N`` in family ``F``, *what is it on?*

Concurrency: allocation is "compute lowest free ID, INSERT". Two concurrent
allocations can pick the same ID; the partial unique index
(``unique_active_apriltag_per_family``) rejects the loser with an
``IntegrityError`` and we retry, exactly like ``maker_boxes`` convert()'s
bin-id allocator. The per-subject partial unique index keeps a record from
getting two active tags even if two requests race on the *same* subject.
"""

from __future__ import annotations

from typing import Optional

from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.db.models import Model, OuterRef, Subquery
from django.utils import timezone

from fiducials.models import DEFAULT_FAMILY, FAMILY_CAPACITY, AprilTagAssignment

# Far above any realistic concurrent allocation load (a handful of kiosks /
# print stations), and bounds the retry runtime. Mirrors the spirit of
# maker_boxes.convert()'s ALLOCATOR_MAX_RETRIES.
ALLOCATOR_MAX_RETRIES = 8


class AprilTagPoolExhausted(RuntimeError):
    """Every ID in the family is currently allocated (active)."""


def _content_type_for(subject: Model) -> ContentType:
    # ``for_concrete_model=True`` (the default) collapses proxy models onto
    # their concrete table, which is what we want for the registry.
    return ContentType.objects.get_for_model(subject.__class__)


def get_active_assignment(subject: Model) -> Optional[AprilTagAssignment]:
    """Return the subject's single active allocation, or None.

    Family-agnostic on purpose: the per-subject partial-unique index lets a
    record hold at most one active tag at a time (across all families), so
    "the active tag" is unambiguous. Renderers read ``.family`` off the
    returned row to draw with the right dictionary even if the system's
    default family is later switched.
    """
    if subject is None or subject.pk is None:
        return None
    ct = _content_type_for(subject)
    return AprilTagAssignment.objects.filter(
        content_type=ct,
        object_id=subject.pk,
        released_at__isnull=True,
    ).first()


def active_tag_id_subquery(model) -> Subquery:
    """Subquery selecting each row's active tag ID, for queryset annotation.

    Lets a serializer expose the tag ID without an N+1::

        qs = Model.objects.annotate(active_april_tag_id=active_tag_id_subquery(Model))

    Each annotated row carries ``active_april_tag_id`` (the int, or None).
    """
    ct = ContentType.objects.get_for_model(model)
    return Subquery(
        AprilTagAssignment.objects.filter(
            content_type=ct,
            object_id=OuterRef("pk"),
            released_at__isnull=True,
        ).values("tag_id")[:1]
    )


def get_active_tag_id(subject: Model) -> Optional[int]:
    """Return the integer tag ID currently on ``subject``, or None.

    A None result means "draw no AprilTag" (a record created before this
    feature, or one whose tag was released).
    """
    assignment = get_active_assignment(subject)
    return assignment.tag_id if assignment is not None else None


def resolve_subject(family: str, tag_id: int) -> Optional[Model]:
    """Return the record a detected tag currently labels, or None.

    The inverse of :func:`allocate_tag`: a camera (or a handheld scanner)
    reports "family ``F``, ID ``N``" and needs the record that ID is on —
    a storage slot, a stint, a maker box. Released allocations are ignored,
    so a recycled ID resolves to whoever holds it *now*.

    Returns None for an unknown/free ID, and also when the subject row was
    hard-deleted without releasing its tag (a dangling GenericForeignKey —
    there is no DB-level cascade), so callers get "nothing there" rather
    than an exception.
    """
    assignment = (
        AprilTagAssignment.objects.filter(
            family=family,
            tag_id=tag_id,
            released_at__isnull=True,
        )
        .select_related("content_type")
        .first()
    )
    if assignment is None:
        return None
    return assignment.subject


def _lowest_free_id(family: str) -> int:
    """Smallest ID in ``range(capacity)`` with no active allocation."""
    capacity = FAMILY_CAPACITY[family]
    used = set(
        AprilTagAssignment.objects.filter(family=family, released_at__isnull=True).values_list(
            "tag_id", flat=True
        )
    )
    if len(used) >= capacity:
        raise AprilTagPoolExhausted(
            f"AprilTag family {family!r} is exhausted: all {capacity} IDs are allocated."
        )
    for candidate in range(capacity):
        if candidate not in used:
            return candidate
    # Unreachable given the length check above, but keeps the contract
    # explicit for callers/readers.
    raise AprilTagPoolExhausted(
        f"AprilTag family {family!r} is exhausted: all {capacity} IDs are allocated."
    )


def allocate_tag(subject: Model, *, family: str = DEFAULT_FAMILY) -> AprilTagAssignment:
    """Idempotently allocate the lowest free AprilTag ID to ``subject``.

    Returns the existing active allocation if the subject already has one
    (so re-running a lifecycle hook is safe). Raises
    :class:`AprilTagPoolExhausted` when the family has no free IDs and
    ``ValueError`` for an unsaved subject or unknown family.
    """
    if subject is None or subject.pk is None:
        raise ValueError("Cannot allocate an AprilTag to an unsaved record.")
    if family not in FAMILY_CAPACITY:
        raise ValueError(f"Unknown AprilTag family {family!r}.")

    existing = get_active_assignment(subject)
    if existing is not None:
        return existing

    ct = _content_type_for(subject)
    last_exc: Optional[IntegrityError] = None
    for _ in range(ALLOCATOR_MAX_RETRIES):
        candidate = _lowest_free_id(family)
        try:
            with transaction.atomic():
                return AprilTagAssignment.objects.create(
                    family=family,
                    tag_id=candidate,
                    content_type=ct,
                    object_id=subject.pk,
                )
        except IntegrityError as exc:
            last_exc = exc
            # Either another allocation grabbed `candidate` (per-family
            # index) or this subject just got a tag concurrently
            # (per-subject index). Resolve the latter; otherwise recompute
            # the lowest free ID and retry.
            existing = get_active_assignment(subject)
            if existing is not None:
                return existing
            continue

    raise AprilTagPoolExhausted(
        f"Failed to allocate an AprilTag in family {family!r} after "
        f"{ALLOCATOR_MAX_RETRIES} attempts (last error: {last_exc})."
    )


def release_tag(subject: Model) -> Optional[AprilTagAssignment]:
    """Retire ``subject``'s active allocation so its ID recycles.

    Returns the row that was released, or None if the subject had no active
    allocation (idempotent — releasing twice is a no-op). Callers should
    invoke this *before* hard-deleting the subject, since the GenericForeignKey
    has no DB-level cascade.
    """
    assignment = get_active_assignment(subject)
    if assignment is None:
        return None
    assignment.released_at = timezone.now()
    assignment.save(update_fields=["released_at"])
    return assignment
