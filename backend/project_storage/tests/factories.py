from __future__ import annotations

from datetime import timedelta

import factory
from django.utils import timezone

from project_storage.models import DEFAULT_STINT_DAYS, ProjectStorageStint


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
