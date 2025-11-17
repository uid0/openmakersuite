"""
Unit tests for checklist models.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

import pytest

from checklists.models import Checklist, ChecklistCompletion, ChecklistStep
from checklists.tests.factories import (
    ChecklistCompletionFactory,
    ChecklistFactory,
    ChecklistStepCompletionFactory,
    ChecklistStepFactory,
)
from inventory.tests.factories import AssetFactory, InventoryItemFactory, LocationFactory

User = get_user_model()


@pytest.mark.unit
class TestChecklistModel:
    """Tests for the Checklist model."""

    def test_checklist_creation(self):
        """Test creating a checklist instance."""
        checklist = ChecklistFactory()
        assert checklist.name is not None
        assert checklist.sig is not None
        assert checklist.is_active is True
        assert checklist.is_public is True
        assert str(checklist) == f"{checklist.name} ({checklist.sig.name})"

    def test_checklist_ordering(self):
        """Test checklists are ordered by name."""
        ChecklistFactory(name="B Checklist")
        ChecklistFactory(name="A Checklist")

        checklists = Checklist.objects.all().order_by("name")
        assert checklists[0].name == "A Checklist"
        assert checklists[1].name == "B Checklist"


@pytest.mark.unit
class TestChecklistStepModel:
    """Tests for the ChecklistStep model."""

    def test_step_creation_with_asset(self):
        """Test creating a step with an asset."""
        asset = AssetFactory()
        step = ChecklistStepFactory(asset=asset, location=None, inventory_item=None)
        assert step.asset == asset
        assert step.location is None
        assert step.inventory_item is None
        assert str(step) == f"{step.checklist.name} - Step {step.step_number}: {step.name}"

    def test_step_creation_with_location(self):
        """Test creating a step with a location."""
        location = LocationFactory()
        # Create step with location, explicitly setting asset to None
        step = ChecklistStep.objects.create(
            checklist=ChecklistFactory(),
            step_number=1,
            name="Test Step",
            asset=None,
            location=location,
            inventory_item=None,
        )
        assert step.location == location
        assert step.asset is None
        assert step.inventory_item is None

    def test_step_creation_with_item(self):
        """Test creating a step with an inventory item."""
        item = InventoryItemFactory()
        # Create step with item, explicitly setting asset and location to None
        step = ChecklistStep.objects.create(
            checklist=ChecklistFactory(),
            step_number=1,
            name="Test Step",
            asset=None,
            location=None,
            inventory_item=item,
        )
        assert step.inventory_item == item
        assert step.asset is None
        assert step.location is None

    def test_step_validation_requires_one_target(self):
        """Test that a step must have exactly one target (asset, location, or item)."""
        checklist = ChecklistFactory()
        step = ChecklistStep(
            checklist=checklist,
            step_number=1,
            name="Test Step",
            asset=None,
            location=None,
            inventory_item=None,
        )

        with pytest.raises(ValidationError):
            step.clean()

    def test_step_validation_rejects_multiple_targets(self):
        """Test that a step cannot have multiple targets."""
        checklist = ChecklistFactory()
        asset = AssetFactory()
        location = LocationFactory()
        step = ChecklistStep(
            checklist=checklist,
            step_number=1,
            name="Test Step",
            asset=asset,
            location=location,
            inventory_item=None,
        )

        with pytest.raises(ValidationError):
            step.clean()

    def test_step_ordering(self):
        """Test steps are ordered by step_number."""
        checklist = ChecklistFactory()
        asset1 = AssetFactory()
        asset2 = AssetFactory()
        asset3 = AssetFactory()
        step3 = ChecklistStep.objects.create(
            checklist=checklist, step_number=3, name="Step 3", asset=asset3
        )
        step1 = ChecklistStep.objects.create(
            checklist=checklist, step_number=1, name="Step 1", asset=asset1
        )
        step2 = ChecklistStep.objects.create(
            checklist=checklist, step_number=2, name="Step 2", asset=asset2
        )

        steps = ChecklistStep.objects.filter(checklist=checklist).order_by("step_number")
        assert steps[0] == step1
        assert steps[1] == step2
        assert steps[2] == step3


@pytest.mark.unit
class TestChecklistCompletionModel:
    """Tests for the ChecklistCompletion model."""

    def test_completion_creation_authenticated(self):
        """Test creating a completion for an authenticated user."""
        user = User.objects.create_user(username="testuser", password="testpass")
        completion = ChecklistCompletionFactory(user=user, user_name="")
        assert completion.user == user
        assert completion.user_name == ""
        assert completion.status == ChecklistCompletion.STATUS_IN_PROGRESS
        assert completion.completed_at is None

    def test_completion_creation_unauthenticated(self):
        """Test creating a completion for an unauthenticated user."""
        completion = ChecklistCompletionFactory(user=None, user_name="John Doe")
        assert completion.user is None
        assert completion.user_name == "John Doe"
        assert completion.status == ChecklistCompletion.STATUS_IN_PROGRESS

    def test_completion_mark_completed(self):
        """Test marking a completion as completed."""
        completion = ChecklistCompletionFactory()
        assert completion.status == ChecklistCompletion.STATUS_IN_PROGRESS
        assert completion.completed_at is None

        completion.mark_completed()
        assert completion.status == ChecklistCompletion.STATUS_COMPLETED
        assert completion.completed_at is not None

    def test_completion_string_representation(self):
        """Test completion string representation."""
        user = User.objects.create_user(username="testuser", password="testpass")
        completion = ChecklistCompletionFactory(user=user)
        assert str(completion) == f"{completion.checklist.name} - {user.username} (in_progress)"


@pytest.mark.unit
class TestChecklistStepCompletionModel:
    """Tests for the ChecklistStepCompletion model."""

    def test_step_completion_creation(self):
        """Test creating a step completion."""
        step = ChecklistStepFactory()  # This will have an asset by default
        completion = ChecklistCompletionFactory(checklist=step.checklist)
        step_completion = ChecklistStepCompletionFactory(completion=completion, step=step)
        assert step_completion.completion == completion
        assert step_completion.step == step
        assert step_completion.scanned_asset == step.asset

    def test_step_completion_string_representation(self):
        """Test step completion string representation."""
        step = ChecklistStepFactory()  # This will have an asset by default
        completion = ChecklistCompletionFactory(checklist=step.checklist, user_name="John")
        step_completion = ChecklistStepCompletionFactory(completion=completion, step=step)
        assert "Step" in str(step_completion)
        assert "John" in str(step_completion)
