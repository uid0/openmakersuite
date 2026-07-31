from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

import factory

from project_storage.models import DEFAULT_STINT_DAYS, ProjectStorageStint, StorageSlot


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
