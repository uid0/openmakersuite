"""
Factories for checklist models.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

import factory

from checklists.models import Checklist, ChecklistCompletion, ChecklistStep, ChecklistStepCompletion
from inventory.tests.factories import AssetFactory

User = get_user_model()


class GroupFactory(factory.django.DjangoModelFactory):
    """Factory for Django Group (SIG)."""

    class Meta:
        model = Group
        django_get_or_create = ("name",)

    name = factory.Sequence(lambda n: f"SIG {n}")


class ChecklistFactory(factory.django.DjangoModelFactory):
    """Factory for Checklist model."""

    class Meta:
        model = Checklist

    name = factory.Sequence(lambda n: f"Checklist {n}")
    description = factory.Faker("text", max_nb_chars=200)
    sig = factory.SubFactory(GroupFactory)
    is_active = True
    is_public = True
    created_by = None


class ChecklistStepFactory(factory.django.DjangoModelFactory):
    """Factory for ChecklistStep model."""

    class Meta:
        model = ChecklistStep

    checklist = factory.SubFactory(ChecklistFactory)
    step_number = factory.Sequence(lambda n: n + 1)
    name = factory.Sequence(lambda n: f"Step {n + 1}")
    asset = factory.LazyFunction(lambda: AssetFactory())
    location = None
    inventory_item = None
    required = True
    notes = factory.Faker("text", max_nb_chars=100)


class ChecklistCompletionFactory(factory.django.DjangoModelFactory):
    """Factory for ChecklistCompletion model."""

    class Meta:
        model = ChecklistCompletion

    checklist = factory.SubFactory(ChecklistFactory)
    user = None
    user_name = ""
    status = ChecklistCompletion.STATUS_IN_PROGRESS


class ChecklistStepCompletionFactory(factory.django.DjangoModelFactory):
    """Factory for ChecklistStepCompletion model."""

    class Meta:
        model = ChecklistStepCompletion

    completion = factory.SubFactory(ChecklistCompletionFactory)
    step = factory.SubFactory(ChecklistStepFactory)
    scanned_asset = None
    scanned_location = None
    scanned_item = None
    notes = ""

    @factory.post_generation
    def set_scanned_item(self, create, extracted, **kwargs):
        """Set scanned item based on step's target."""
        if not create:
            return

        # Only set if not already set
        if not self.scanned_asset and not self.scanned_location and not self.scanned_item:
            if self.step.asset:
                self.scanned_asset = self.step.asset
            elif self.step.location:
                self.scanned_location = self.step.location
            elif self.step.inventory_item:
                self.scanned_item = self.step.inventory_item

            self.save()
