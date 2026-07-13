"""Tests for the reusable typed-target abstraction (#884).

Covers the mixin's pure helpers (``count_present_targets``, the exactly-one
CheckConstraint builder, the ``TargetField`` dataclass) plus its behaviour on the
flagship adopter ``ChecklistStep`` — the accessor tokens, the exactly-one
``clean()`` validation, and (new) the DB-level CheckConstraint.
"""

from dataclasses import FrozenInstanceError

from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction

import pytest

from checklists.models import ChecklistStep
from checklists.tests.factories import ChecklistFactory
from inventory.models import TargetField, TypedTargetModel, count_present_targets
from inventory.tests.factories import AssetFactory, InventoryItemFactory, LocationFactory


@pytest.mark.unit
class TestTypedTargetHelpers:
    """Pure-Python helpers — no database."""

    def test_count_present_targets_counts_truthy(self):
        assert count_present_targets([None, "", 0]) == 0
        assert count_present_targets([None, "x", 0]) == 1
        assert count_present_targets(["a", 5, object()]) == 3

    def test_target_field_defaults(self):
        spec = TargetField("asset", "asset")
        assert spec.token == "asset"
        assert spec.field == "asset"
        assert spec.value is None
        assert spec.has_object is True
        # frozen dataclass — assignment is rejected
        with pytest.raises(FrozenInstanceError):
            spec.token = "nope"

    def test_exactly_one_constraint_shape(self):
        fields = (
            TargetField("asset", "asset"),
            TargetField("location", "location"),
            TargetField("inventory_item", "inventory_item"),
        )
        constraint = TypedTargetModel.exactly_one_constraint("demo_one", fields)
        assert isinstance(constraint, models.CheckConstraint)
        assert constraint.name == "demo_one"
        rendered = str(constraint.condition)
        # every declared field participates in the isnull disjunction
        for name in ("asset", "location", "inventory_item"):
            assert f"{name}__isnull" in rendered


@pytest.mark.django_db
class TestChecklistStepTypedTarget:
    """The mixin as adopted by the flagship ChecklistStep."""

    def test_target_and_type_asset(self):
        asset = AssetFactory()
        step = ChecklistStep(checklist=ChecklistFactory(), step_number=1, name="s", asset=asset)
        assert step.target == asset
        assert step.target_type == "asset"

    def test_target_and_type_location(self):
        location = LocationFactory()
        step = ChecklistStep(
            checklist=ChecklistFactory(), step_number=1, name="s", location=location
        )
        assert step.target == location
        assert step.target_type == "location"

    def test_target_and_type_inventory_item(self):
        item = InventoryItemFactory()
        step = ChecklistStep(
            checklist=ChecklistFactory(), step_number=1, name="s", inventory_item=item
        )
        assert step.target == item
        assert step.target_type == "inventory_item"

    def test_target_none_when_unset(self):
        step = ChecklistStep(checklist=ChecklistFactory(), step_number=1, name="s")
        assert step.target is None
        assert step.target_type is None

    def test_clean_requires_exactly_one(self):
        checklist = ChecklistFactory()
        # zero targets
        with pytest.raises(ValidationError):
            ChecklistStep(checklist=checklist, step_number=1, name="s").clean()
        # two targets
        with pytest.raises(ValidationError):
            ChecklistStep(
                checklist=checklist,
                step_number=1,
                name="s",
                asset=AssetFactory(),
                location=LocationFactory(),
            ).clean()
        # exactly one is fine
        ChecklistStep(checklist=checklist, step_number=1, name="s", asset=AssetFactory()).clean()

    def test_db_check_constraint_rejects_two_targets(self):
        """The new DB CheckConstraint blocks a 2-target row even when clean() is
        bypassed (bulk_create). Regression guard: before #884 the exactly-one rule
        lived only in Python."""
        checklist = ChecklistFactory()
        asset = AssetFactory()
        location = LocationFactory()
        bad = ChecklistStep(
            checklist=checklist,
            step_number=99,
            name="two targets",
            asset=asset,
            location=location,
        )
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ChecklistStep.objects.bulk_create([bad])

    def test_db_check_constraint_rejects_zero_targets(self):
        """Exactly-one also forbids the all-null row at the DB layer."""
        checklist = ChecklistFactory()
        empty = ChecklistStep(checklist=checklist, step_number=98, name="no target")
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ChecklistStep.objects.bulk_create([empty])
