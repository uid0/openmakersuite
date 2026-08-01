from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

import factory

from project_storage.models import (
    DEFAULT_STINT_DAYS,
    ProjectStorageStint,
    StorageAssignment,
    StorageSlot,
)


class ProjectStorageStintFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ProjectStorageStint

    username = factory.Sequence(lambda n: f"member{n}")
    first_name = "Pat"
    last_name = "Member"
    email = factory.LazyAttribute(lambda o: f"{o.username}@example.com")
    project_title = "Test project"
    started_at = factory.LazyFunction(timezone.now)
    expires_at = factory.LazyAttribute(lambda o: o.started_at + timedelta(days=DEFAULT_STINT_DAYS))
    storage_location_name = "Project Shelf A"


class StorageSlotFactory(factory.django.DjangoModelFactory):
    """A slot on rack 1, level A, walking up the positions (1A1, 1A2, …).

    ``code`` is deliberately not set — the model derives it from the
    components on save.
    """

    class Meta:
        model = StorageSlot

    rack = 1
    level = "A"
    position = factory.Sequence(lambda n: n + 1)


class StorageAssignmentFactory(factory.django.DjangoModelFactory):
    """A live logistics holding of a fresh slot.

    Logistics is the default because it's the type that needs no related
    object — a committee assignment wants a real ``owning_group``, which the
    test that cares about it should pass explicitly.
    """

    class Meta:
        model = StorageAssignment

    slot = factory.SubFactory(StorageSlotFactory)
    storage_type = StorageAssignment.TYPE_LOGISTICS
    occupant_label = "Logistics crew"
    assigned_at = factory.LazyFunction(timezone.now)
