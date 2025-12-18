"""
Tests for AssetPart deletion with AssetProblem foreign key constraints.

This test suite ensures that deleting an AssetPart that has AssetProblem
records referencing it does not cause a foreign key constraint violation.
The part field in AssetProblem should be set to NULL when the AssetPart is deleted.
"""

from django.db import IntegrityError

import pytest

from inventory.models import AssetPart, AssetProblem
from inventory.tests.factories import (
    AssetFactory,
    AssetPartFactory,
    AssetProblemFactory,
    InventoryItemFactory,
)


@pytest.mark.django_db
class TestAssetPartDeletionWithAssetProblems:
    """Test deletion of AssetPart when AssetProblem records reference it."""

    def test_delete_asset_part_with_referenced_problem_sets_part_to_null(self):
        """
        Test that deleting an AssetPart that has an AssetProblem referencing it
        successfully sets the AssetProblem's part field to NULL instead of raising
        a foreign key constraint violation.
        """
        # Create an asset and an asset part
        asset = AssetFactory()
        part_item = InventoryItemFactory()
        asset_part = AssetPartFactory(asset=asset, part=part_item)

        # Create an AssetProblem that references the AssetPart
        problem = AssetProblemFactory(asset=asset, part=asset_part)

        # Verify the problem references the part
        assert problem.part == asset_part
        assert problem.part_id == asset_part.id

        # Delete the AssetPart - this should succeed and set problem.part to NULL
        asset_part.delete()

        # Refresh the problem from the database
        problem.refresh_from_db()

        # Verify the part field was set to NULL
        assert problem.part is None
        assert problem.part_id is None

        # Verify the problem still exists and is otherwise intact
        assert problem.asset == asset
        assert problem.description is not None
        assert AssetProblem.objects.filter(id=problem.id).exists()

    def test_delete_asset_part_with_multiple_referenced_problems(self):
        """
        Test that deleting an AssetPart that has multiple AssetProblem records
        referencing it successfully sets all AssetProblem part fields to NULL.
        """
        # Create an asset and an asset part
        asset = AssetFactory()
        part_item = InventoryItemFactory()
        asset_part = AssetPartFactory(asset=asset, part=part_item)

        # Create multiple AssetProblems that reference the AssetPart
        problem1 = AssetProblemFactory(asset=asset, part=asset_part)
        problem2 = AssetProblemFactory(asset=asset, part=asset_part)
        problem3 = AssetProblemFactory(asset=asset, part=asset_part)

        # Verify all problems reference the part
        assert problem1.part == asset_part
        assert problem2.part == asset_part
        assert problem3.part == asset_part

        # Delete the AssetPart
        asset_part.delete()

        # Refresh all problems from the database
        problem1.refresh_from_db()
        problem2.refresh_from_db()
        problem3.refresh_from_db()

        # Verify all part fields were set to NULL
        assert problem1.part is None
        assert problem2.part is None
        assert problem3.part is None

        # Verify all problems still exist
        assert AssetProblem.objects.filter(id=problem1.id).exists()
        assert AssetProblem.objects.filter(id=problem2.id).exists()
        assert AssetProblem.objects.filter(id=problem3.id).exists()

    def test_delete_asset_part_without_referenced_problems_succeeds(self):
        """
        Test that deleting an AssetPart that has no AssetProblem records
        referencing it succeeds normally.
        """
        # Create an asset and an asset part
        asset = AssetFactory()
        part_item = InventoryItemFactory()
        asset_part = AssetPartFactory(asset=asset, part=part_item)

        # Create an AssetProblem that does NOT reference the part
        problem = AssetProblemFactory(asset=asset, part=None)

        # Verify the problem doesn't reference the part
        assert problem.part is None

        # Delete the AssetPart - this should succeed normally
        asset_part_id = asset_part.id
        asset_part.delete()

        # Verify the AssetPart was deleted
        assert not AssetPart.objects.filter(id=asset_part_id).exists()

        # Verify the problem is still intact
        problem.refresh_from_db()
        assert problem.asset == asset
        assert problem.part is None

    def test_delete_asset_part_via_admin_inline_simulation(self):
        """
        Test simulating the Django admin inline deletion scenario.
        This tests the actual use case where a user deletes an AssetPart
        from the asset change page in the admin.
        """
        # Create an asset with a part
        asset = AssetFactory()
        part_item = InventoryItemFactory()
        asset_part = AssetPartFactory(asset=asset, part=part_item)

        # Create problems that reference the part
        problem1 = AssetProblemFactory(asset=asset, part=asset_part)
        problem2 = AssetProblemFactory(asset=asset, part=asset_part)

        # Simulate admin inline deletion: delete the AssetPart directly
        # This is what happens when you click delete on an inline in Django admin
        asset_part.delete()

        # Verify both problems have their part set to NULL
        problem1.refresh_from_db()
        problem2.refresh_from_db()

        assert problem1.part is None
        assert problem2.part is None

        # Verify the problems are still associated with the asset
        assert problem1.asset == asset
        assert problem2.asset == asset

    def test_asset_problem_can_exist_without_part(self):
        """
        Test that AssetProblem records can exist without a part reference.
        This ensures the part field is truly optional.
        """
        asset = AssetFactory()
        problem = AssetProblemFactory(asset=asset, part=None)

        assert problem.part is None
        assert problem.asset == asset
        assert problem.description is not None

    def test_asset_problem_can_have_part_reference(self):
        """
        Test that AssetProblem records can have a part reference.
        This ensures the part field works correctly when set.
        """
        asset = AssetFactory()
        part_item = InventoryItemFactory()
        asset_part = AssetPartFactory(asset=asset, part=part_item)
        problem = AssetProblemFactory(asset=asset, part=asset_part)

        assert problem.part == asset_part
        assert problem.part.asset == asset
        assert problem.asset == asset

    def test_database_constraint_is_set_null(self):
        """
        Test that the database foreign key constraint is actually set to SET NULL.
        This verifies the migration correctly set up the constraint.
        This test would fail if the constraint was CASCADE, RESTRICT, or PROTECT.
        """
        from django.db import connection

        # Only test on PostgreSQL where we can inspect constraints
        if connection.vendor != "postgresql":
            pytest.skip("Constraint inspection only works on PostgreSQL")

        asset = AssetFactory()
        part_item = InventoryItemFactory()
        asset_part = AssetPartFactory(asset=asset, part=part_item)
        # Create a problem to ensure the constraint exists (we don't need to use it)
        AssetProblemFactory(asset=asset, part=asset_part)

        # Verify the constraint exists and is SET NULL
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    tc.constraint_name,
                    rc.delete_rule
                FROM information_schema.table_constraints tc
                JOIN information_schema.referential_constraints rc
                    ON tc.constraint_name = rc.constraint_name
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                WHERE tc.table_name = 'inventory_assetproblem'
                    AND tc.constraint_type = 'FOREIGN KEY'
                    AND kcu.column_name = 'part_id'
            """
            )
            result = cursor.fetchone()

            assert result is not None, "Foreign key constraint should exist"
            constraint_name, delete_rule = result
            assert (
                delete_rule == "SET NULL"
            ), f"Expected delete_rule to be 'SET NULL', got '{delete_rule}'. "
            "This would cause foreign key constraint violations when deleting AssetPart."

    def test_model_on_delete_is_set_null(self):
        """
        Test that the model's on_delete behavior is SET_NULL.
        This catches regressions where someone might accidentally change
        the on_delete behavior in the model definition.
        """
        from django.db import models

        # Get the field from the model
        part_field = AssetProblem._meta.get_field("part")

        assert isinstance(part_field, models.ForeignKey), "part field should be a ForeignKey"
        assert part_field.remote_field.on_delete == models.SET_NULL, (
            "part field's on_delete should be SET_NULL, not "
            f"{part_field.remote_field.on_delete}. "
            "This would cause foreign key constraint violations when deleting AssetPart."
        )
        assert part_field.null is True, "part field should be nullable"
        assert part_field.blank is True, "part field should be blankable"

    def test_delete_asset_part_does_not_raise_integrity_error(self):
        """
        Test that deleting an AssetPart with referenced problems does NOT raise
        an IntegrityError. This is the key regression test - if this fails,
        the original bug has returned.
        """
        asset = AssetFactory()
        part_item = InventoryItemFactory()
        asset_part = AssetPartFactory(asset=asset, part=part_item)

        # Create multiple problems referencing the part
        problems = [AssetProblemFactory(asset=asset, part=asset_part) for _ in range(3)]

        # This should NOT raise IntegrityError
        # If it does, the constraint is wrong (likely CASCADE, RESTRICT, or PROTECT)
        try:
            asset_part.delete()
        except IntegrityError as e:
            pytest.fail(
                f"Deleting AssetPart raised IntegrityError: {e}. "
                "This indicates the foreign key constraint is not set to SET NULL. "
                "The constraint should allow deletion and set part_id to NULL."
            )

        # Verify all problems still exist with part set to NULL
        for problem in problems:
            problem.refresh_from_db()
            assert problem.part is None, "Problem's part should be NULL after AssetPart deletion"
            assert problem.asset == asset, "Problem should still reference the asset"

    def test_bulk_delete_asset_part_with_referenced_problems(self):
        """
        Test bulk deletion of AssetPart records when problems reference them.
        This tests a different deletion path that might behave differently.
        """
        asset = AssetFactory()
        part_item1 = InventoryItemFactory()
        part_item2 = InventoryItemFactory()
        asset_part1 = AssetPartFactory(asset=asset, part=part_item1)
        asset_part2 = AssetPartFactory(asset=asset, part=part_item2)

        # Create problems referencing both parts
        problem1 = AssetProblemFactory(asset=asset, part=asset_part1)
        problem2 = AssetProblemFactory(asset=asset, part=asset_part2)

        # Bulk delete both parts
        AssetPart.objects.filter(id__in=[asset_part1.id, asset_part2.id]).delete()

        # Verify both problems have their part set to NULL
        problem1.refresh_from_db()
        problem2.refresh_from_db()

        assert problem1.part is None
        assert problem2.part is None
        assert problem1.asset == asset
        assert problem2.asset == asset
