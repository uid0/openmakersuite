"""
Tests for dimensional calculations and admin functionality for ItemSupplier.

This test suite covers:
- Dimensional property calculations (volume, unit weight, display formatting)
- Edge cases and error handling
- Admin interface functionality
- US unit conversions
"""

from decimal import Decimal

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from inventory.admin import ItemSupplierAdmin, ItemSupplierInline
from inventory.models import ItemSupplier
from inventory.tests.factories import (CategoryFactory, InventoryItemFactory,
                                       ItemSupplierFactory, SupplierFactory)

User = get_user_model()


class TestDimensionalCalculations(TestCase):
    """Test dimensional property calculations on ItemSupplier model."""

    def setUp(self):
        """Set up test data."""
        self.item = InventoryItemFactory()
        self.supplier = SupplierFactory()

    def test_package_volume_calculation(self):
        """Test package volume calculation in cubic inches."""
        item_supplier = ItemSupplierFactory(
            item=self.item,
            supplier=self.supplier,
            package_height=Decimal("6.0"),  # inches
            package_width=Decimal("8.0"),  # inches
            package_length=Decimal("12.0"),  # inches
            quantity_per_package=24,
        )

        expected_volume = Decimal("6.0") * Decimal("8.0") * Decimal("12.0")  # 576.0 cubic inches
        self.assertEqual(item_supplier.package_volume, expected_volume)
        self.assertEqual(float(item_supplier.package_volume), 576.0)

    def test_package_volume_with_missing_dimensions(self):
        """Test that package volume returns None when dimensions are missing."""
        # Missing height
        item_supplier = ItemSupplierFactory(
            item=self.item,
            supplier=self.supplier,
            package_height=None,
            package_width=Decimal("8.0"),
            package_length=Decimal("12.0"),
        )
        self.assertIsNone(item_supplier.package_volume)

        # Missing width
        item_supplier.package_height = Decimal("6.0")
        item_supplier.package_width = None
        self.assertIsNone(item_supplier.package_volume)

        # Missing length
        item_supplier.package_width = Decimal("8.0")
        item_supplier.package_length = None
        self.assertIsNone(item_supplier.package_volume)

    def test_package_volume_with_zero_dimensions(self):
        """Test package volume calculation with zero dimensions."""
        item_supplier = ItemSupplierFactory(
            item=self.item,
            supplier=self.supplier,
            package_height=Decimal("0.0"),
            package_width=Decimal("8.0"),
            package_length=Decimal("12.0"),
        )

        self.assertEqual(item_supplier.package_volume, Decimal("0.0"))

    def test_unit_weight_calculation(self):
        """Test unit weight calculation in ounces."""
        item_supplier = ItemSupplierFactory(
            item=self.item,
            supplier=self.supplier,
            package_weight=Decimal("2.5"),  # pounds
            quantity_per_package=20,
        )

        # 2.5 lbs * 16 oz/lb = 40 oz total
        # 40 oz / 20 units = 2.0 oz per unit
        expected_unit_weight = Decimal("2.0")
        self.assertEqual(item_supplier.unit_weight, expected_unit_weight)

    def test_unit_weight_with_fractional_result(self):
        """Test unit weight calculation with fractional results."""
        item_supplier = ItemSupplierFactory(
            item=self.item,
            supplier=self.supplier,
            package_weight=Decimal("1.0"),  # pounds (16 oz)
            quantity_per_package=3,  # units
        )

        # 1.0 lb * 16 oz/lb = 16 oz total
        # 16 oz / 3 units = 5.333... oz per unit
        expected_unit_weight = Decimal("16") / Decimal("3")
        self.assertEqual(item_supplier.unit_weight, expected_unit_weight)
        self.assertAlmostEqual(float(item_supplier.unit_weight), 5.333333, places=5)

    def test_unit_weight_with_missing_data(self):
        """Test unit weight returns None when data is missing."""
        # Missing package weight
        item_supplier = ItemSupplierFactory(
            item=self.item, supplier=self.supplier, package_weight=None, quantity_per_package=10
        )
        self.assertIsNone(item_supplier.unit_weight)

        # Zero quantity per package
        item_supplier.package_weight = Decimal("2.0")
        item_supplier.quantity_per_package = 0
        self.assertIsNone(item_supplier.unit_weight)

    def test_package_dimensions_display_complete(self):
        """Test package dimensions display with all dimensions provided."""
        item_supplier = ItemSupplierFactory(
            item=self.item,
            supplier=self.supplier,
            package_height=Decimal("6.5"),
            package_width=Decimal("8.25"),
            package_length=Decimal("12.75"),
            package_weight=Decimal("2.5"),
        )

        expected = 'L: 12.75" | W: 8.25" | H: 6.5" | Weight: 2.5 lbs'
        self.assertEqual(item_supplier.package_dimensions_display, expected)

    def test_package_dimensions_display_partial(self):
        """Test package dimensions display with only some dimensions."""
        item_supplier = ItemSupplierFactory(
            item=self.item,
            supplier=self.supplier,
            package_length=Decimal("12.0"),
            package_width=Decimal("8.0"),
            package_height=None,  # Missing height
            package_weight=Decimal("1.5"),
        )

        expected = 'L: 12.0" | W: 8.0" | Weight: 1.5 lbs'
        self.assertEqual(item_supplier.package_dimensions_display, expected)

    def test_package_dimensions_display_empty(self):
        """Test package dimensions display with no dimensions."""
        item_supplier = ItemSupplierFactory(
            item=self.item,
            supplier=self.supplier,
            package_height=None,
            package_width=None,
            package_length=None,
            package_weight=None,
        )

        expected = "No dimensions specified"
        self.assertEqual(item_supplier.package_dimensions_display, expected)

    def test_package_dimensions_display_weight_only(self):
        """Test package dimensions display with only weight."""
        item_supplier = ItemSupplierFactory(
            item=self.item,
            supplier=self.supplier,
            package_height=None,
            package_width=None,
            package_length=None,
            package_weight=Decimal("0.75"),
        )

        expected = "Weight: 0.75 lbs"
        self.assertEqual(item_supplier.package_dimensions_display, expected)

    def test_decimal_precision_handling(self):
        """Test that decimal precision is handled correctly in calculations."""
        item_supplier = ItemSupplierFactory(
            item=self.item,
            supplier=self.supplier,
            package_height=Decimal("6.125"),  # 6 1/8 inches
            package_width=Decimal("8.375"),  # 8 3/8 inches
            package_length=Decimal("12.0625"),  # 12 1/16 inches
            package_weight=Decimal("2.375"),  # 2 3/8 pounds
            quantity_per_package=15,
        )

        # Volume calculation
        expected_volume = Decimal("6.125") * Decimal("8.375") * Decimal("12.0625")
        self.assertEqual(item_supplier.package_volume, expected_volume)

        # Unit weight calculation: 2.375 lbs * 16 oz/lb / 15 units = 2.533... oz/unit
        expected_unit_weight = (Decimal("2.375") * Decimal("16")) / Decimal("15")
        self.assertEqual(item_supplier.unit_weight, expected_unit_weight)


class TestDimensionalAdminInterface(TestCase):
    """Test admin interface functionality for dimensional data."""

    def setUp(self):
        """Set up test data."""
        self.site = AdminSite()
        self.admin = ItemSupplierAdmin(ItemSupplier, self.site)
        self.inline = ItemSupplierInline(ItemSupplier, self.site)

        self.item = InventoryItemFactory()
        self.supplier = SupplierFactory()
        self.item_supplier = ItemSupplierFactory(
            item=self.item,
            supplier=self.supplier,
            package_height=Decimal("6.0"),
            package_width=Decimal("8.0"),
            package_length=Decimal("10.0"),
            package_weight=Decimal("2.0"),
            quantity_per_package=12,
        )

        # Create admin user
        self.admin_user = User.objects.create_superuser(
            username="admin", email="admin@test.com", password="adminpass"
        )

    def test_admin_package_dimensions_display(self):
        """Test admin method for displaying package dimensions."""
        result = self.admin.package_dimensions_display(self.item_supplier)
        expected = 'L: 10.0" | W: 8.0" | H: 6.0" | Weight: 2.0 lbs'
        self.assertEqual(result, expected)

        # Test with None object
        result = self.admin.package_dimensions_display(None)
        self.assertEqual(result, "—")

    def test_admin_package_volume_display(self):
        """Test admin method for displaying package volume."""
        result = self.admin.package_volume_display(self.item_supplier)
        # 6.0 * 8.0 * 10.0 = 480.0 cubic inches
        expected = "480.00 in³"
        self.assertEqual(result, expected)

        # Test with no volume
        self.item_supplier.package_height = None
        result = self.admin.package_volume_display(self.item_supplier)
        self.assertEqual(result, "—")

    def test_admin_unit_weight_display(self):
        """Test admin method for displaying unit weight."""
        result = self.admin.unit_weight_display(self.item_supplier)
        # 2.0 lbs * 16 oz/lb / 12 units = 2.667 oz/unit
        expected = "2.667 oz"
        self.assertEqual(result, expected)

        # Test with no unit weight
        self.item_supplier.package_weight = None
        result = self.admin.unit_weight_display(self.item_supplier)
        self.assertEqual(result, "—")

    def test_inline_admin_methods(self):
        """Test inline admin display methods."""
        # Test package dimensions display
        result = self.inline.package_dimensions_display(self.item_supplier)
        expected = 'L: 10.0" | W: 8.0" | H: 6.0" | Weight: 2.0 lbs'
        self.assertEqual(result, expected)

        # Test package volume display
        result = self.inline.package_volume_display(self.item_supplier)
        expected = "480.00 in³"
        self.assertEqual(result, expected)

        # Test unit weight display
        result = self.inline.unit_weight_display(self.item_supplier)
        expected = "2.667 oz"
        self.assertEqual(result, expected)

    def test_admin_fieldsets_include_dimensional_fields(self):
        """Test that admin fieldsets include the dimensional fields for editing."""
        fieldsets = self.admin.fieldsets

        # Find the Package Dimensions fieldset
        package_dimensions_fieldset = None
        for name, options in fieldsets:
            if name == "Package Dimensions":
                package_dimensions_fieldset = options
                break

        self.assertIsNotNone(package_dimensions_fieldset)
        expected_fields = ("package_height", "package_width", "package_length", "package_weight")
        self.assertEqual(package_dimensions_fieldset["fields"], expected_fields)

    def test_inline_fields_include_dimensional_fields(self):
        """Test that inline includes dimensional fields for editing."""
        fields = self.inline.fields

        # Check that all dimensional fields are included
        self.assertIn("package_height", fields)
        self.assertIn("package_width", fields)
        self.assertIn("package_length", fields)
        self.assertIn("package_weight", fields)

        # Check that calculated fields are readonly
        readonly_fields = self.inline.readonly_fields
        self.assertIn("package_dimensions_display", readonly_fields)
        self.assertIn("package_volume_display", readonly_fields)
        self.assertIn("unit_weight_display", readonly_fields)


class TestDimensionalEdgeCases(TestCase):
    """Test edge cases and error conditions for dimensional calculations."""

    def setUp(self):
        """Set up test data."""
        self.item = InventoryItemFactory()
        self.supplier = SupplierFactory()

    def test_very_large_dimensions(self):
        """Test calculations with very large dimensional values."""
        item_supplier = ItemSupplierFactory(
            item=self.item,
            supplier=self.supplier,
            package_height=Decimal("100.0"),
            package_width=Decimal("50.0"),
            package_length=Decimal("200.0"),
            package_weight=Decimal("500.0"),
            quantity_per_package=1000,
        )

        # Volume: 100 * 50 * 200 = 1,000,000 cubic inches
        expected_volume = Decimal("1000000")
        self.assertEqual(item_supplier.package_volume, expected_volume)

        # Unit weight: 500 lbs * 16 oz/lb / 1000 units = 8.0 oz/unit
        expected_unit_weight = Decimal("8.0")
        self.assertEqual(item_supplier.unit_weight, expected_unit_weight)

    def test_very_small_dimensions(self):
        """Test calculations with very small dimensional values."""
        item_supplier = ItemSupplierFactory(
            item=self.item,
            supplier=self.supplier,
            package_height=Decimal("0.125"),  # 1/8 inch
            package_width=Decimal("0.25"),  # 1/4 inch
            package_length=Decimal("0.5"),  # 1/2 inch
            package_weight=Decimal("0.0625"),  # 1/16 pound (1 oz)
            quantity_per_package=4,
        )

        # Volume: 0.125 * 0.25 * 0.5 = 0.015625 cubic inches
        expected_volume = Decimal("0.015625")
        self.assertEqual(item_supplier.package_volume, expected_volume)

        # Unit weight: 0.0625 lbs * 16 oz/lb / 4 units = 0.25 oz/unit
        expected_unit_weight = Decimal("0.25")
        self.assertEqual(item_supplier.unit_weight, expected_unit_weight)

    def test_single_unit_package(self):
        """Test calculations for single-unit packages."""
        item_supplier = ItemSupplierFactory(
            item=self.item,
            supplier=self.supplier,
            package_height=Decimal("3.0"),
            package_width=Decimal("2.0"),
            package_length=Decimal("4.0"),
            package_weight=Decimal("0.5"),
            quantity_per_package=1,  # Single unit
        )

        # Volume: 3 * 2 * 4 = 24 cubic inches
        expected_volume = Decimal("24")
        self.assertEqual(item_supplier.package_volume, expected_volume)

        # Unit weight: 0.5 lbs * 16 oz/lb / 1 unit = 8.0 oz/unit
        expected_unit_weight = Decimal("8.0")
        self.assertEqual(item_supplier.unit_weight, expected_unit_weight)

    def test_package_dimensions_display_ordering(self):
        """Test that package dimensions are displayed in correct order (L, W, H, Weight)."""
        item_supplier = ItemSupplierFactory(
            item=self.item,
            supplier=self.supplier,
            package_height=Decimal("1.0"),  # Should be 3rd
            package_width=Decimal("2.0"),  # Should be 2nd
            package_length=Decimal("3.0"),  # Should be 1st
            package_weight=Decimal("4.0"),  # Should be 4th
        )

        result = item_supplier.package_dimensions_display
        # Should be ordered as: Length, Width, Height, Weight
        expected = 'L: 3.0" | W: 2.0" | H: 1.0" | Weight: 4.0 lbs'
        self.assertEqual(result, expected)


@pytest.mark.integration
class TestDimensionalIntegration(TestCase):
    """Integration tests for dimensional functionality across the system."""

    def setUp(self):
        """Set up test data."""
        self.admin_user = User.objects.create_superuser(
            username="admin", email="admin@test.com", password="adminpass"
        )
        self.item = InventoryItemFactory()
        self.supplier = SupplierFactory()

    def test_dimensional_data_persistence(self):
        """Test that dimensional data persists correctly through save/load cycles."""
        # Create with dimensional data (using precision compatible with field definitions)
        item_supplier = ItemSupplierFactory(
            item=self.item,
            supplier=self.supplier,
            package_height=Decimal("6.25"),  # 2 decimal places
            package_width=Decimal("8.75"),  # 2 decimal places
            package_length=Decimal("12.12"),  # 2 decimal places (field limit)
            package_weight=Decimal("3.500"),  # 3 decimal places
            quantity_per_package=20,
        )

        # Save and reload
        item_supplier.save()
        reloaded = ItemSupplier.objects.get(pk=item_supplier.pk)

        # Verify dimensional data (respecting field precision)
        self.assertEqual(reloaded.package_height, Decimal("6.25"))
        self.assertEqual(reloaded.package_width, Decimal("8.75"))
        self.assertEqual(reloaded.package_length, Decimal("12.12"))
        self.assertEqual(reloaded.package_weight, Decimal("3.500"))

        # Verify calculated properties
        expected_volume = Decimal("6.25") * Decimal("8.75") * Decimal("12.12")
        self.assertEqual(reloaded.package_volume, expected_volume)

        expected_unit_weight = (Decimal("3.500") * Decimal("16")) / Decimal("20")
        self.assertEqual(reloaded.unit_weight, expected_unit_weight)

    def test_api_serialization_includes_dimensional_data(self):
        """Test that API serialization includes dimensional data and calculations."""
        from inventory.serializers import ItemSupplierSerializer

        item_supplier = ItemSupplierFactory(
            item=self.item,
            supplier=self.supplier,
            package_height=Decimal("5.00"),
            package_width=Decimal("7.00"),
            package_length=Decimal("9.00"),
            package_weight=Decimal("2.000"),
            quantity_per_package=10,
        )

        serializer = ItemSupplierSerializer(item_supplier)
        data = serializer.data

        # Check that dimensional fields are serialized
        self.assertEqual(data["package_height"], "5.00")
        self.assertEqual(data["package_width"], "7.00")
        self.assertEqual(data["package_length"], "9.00")
        self.assertEqual(data["package_weight"], "2.000")

        # Check that calculated fields are included
        expected_volume = Decimal("5.00") * Decimal("7.00") * Decimal("9.00")  # 315.00
        self.assertEqual(data["package_volume"], "315.00")

        expected_unit_weight = (Decimal("2.000") * Decimal("16")) / Decimal("10")  # 3.200
        self.assertEqual(data["unit_weight"], "3.200")

        self.assertEqual(
            data["package_dimensions_display"], 'L: 9.00" | W: 7.00" | H: 5.00" | Weight: 2.000 lbs'
        )
