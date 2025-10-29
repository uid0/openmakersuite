"""
Tests for the new package cost vs unit cost logic.
"""

from decimal import Decimal

import pytest
from inventory.models import InventoryItem, ItemSupplier, Supplier


@pytest.mark.django_db
class TestNewCostingLogic:
    """Test the new costing workflow where users enter package cost."""

    def test_trash_bag_example(self):
        """Test the specific example from the user: 50-count box for $40.49."""

        supplier = Supplier.objects.create(name="Office Supply Co", supplier_type=Supplier.ONLINE)
        item = InventoryItem.objects.create(
            name="Trash Bags",
            description="Heavy-duty 13-gallon trash bags",
            reorder_quantity=5,
            current_stock=2,
            minimum_stock=3,
        )

        # User enters package cost (what they actually pay)
        item_supplier = ItemSupplier.objects.create(
            item=item,
            supplier=supplier,
            supplier_sku="TB-50",
            quantity_per_package=50,
            package_cost=Decimal("40.49"),
        )

        # Unit cost should be calculated: $40.49 / 50 = $0.8098
        expected_unit_cost = Decimal("40.49") / Decimal("50")
        assert item_supplier.unit_cost == expected_unit_cost

        # Verify the calculation is approximately $0.81
        assert abs(item_supplier.unit_cost - Decimal("0.8098")) < Decimal("0.0001")

    def test_cost_calculation_precision(self):
        """Test that cost calculations maintain proper decimal precision."""

        supplier = Supplier.objects.create(name="Hardware Store", supplier_type=Supplier.LOCAL)
        item = InventoryItem.objects.create(
            name="Screws",
            description="Phillips head screws #8",
            reorder_quantity=10,
            current_stock=50,
            minimum_stock=20,
        )

        # Test with package that doesn't divide evenly
        item_supplier = ItemSupplier.objects.create(
            item=item,
            supplier=supplier,
            supplier_sku="SCR-100",
            quantity_per_package=100,
            package_cost=Decimal("12.99"),
        )

        # Unit cost should be $12.99 / 100 = $0.1299
        assert item_supplier.unit_cost == Decimal("0.1299")

    def test_inventory_item_total_value_with_new_costing(self):
        """Test that inventory value calculations work correctly with new costing."""

        supplier = Supplier.objects.create(name="Electronics Shop", supplier_type=Supplier.ONLINE)
        item = InventoryItem.objects.create(
            name="Resistors",
            description="1K ohm resistors",
            reorder_quantity=100,
            current_stock=500,  # 500 individual resistors in stock
            minimum_stock=50,
        )

        # Package of 100 resistors costs $5.00
        ItemSupplier.objects.create(
            item=item,
            supplier=supplier,
            supplier_sku="RES-1K",
            quantity_per_package=100,
            package_cost=Decimal("5.00"),
            is_primary=True,
        )

        # Total value should be: 500 units * ($5.00 / 100) = 500 * $0.05 = $25.00
        assert item.total_value == Decimal("25.00")

    def test_updating_package_cost_recalculates_unit_cost(self):
        """Test that updating package cost recalculates unit cost."""

        supplier = Supplier.objects.create(name="Tool Supply", supplier_type=Supplier.LOCAL)
        item = InventoryItem.objects.create(
            name="Drill Bits",
            description="HSS drill bit set",
            reorder_quantity=1,
            current_stock=5,
            minimum_stock=2,
        )

        item_supplier = ItemSupplier.objects.create(
            item=item,
            supplier=supplier,
            supplier_sku="DB-SET",
            quantity_per_package=10,
            package_cost=Decimal("29.99"),
        )

        # Initial unit cost: $29.99 / 10 = $2.999
        assert item_supplier.unit_cost == Decimal("2.999")

        # Update package cost
        item_supplier.package_cost = Decimal("24.99")
        item_supplier.save()

        # Unit cost should be recalculated: $24.99 / 10 = $2.499
        assert item_supplier.unit_cost == Decimal("2.499")

    def test_edge_case_quantity_per_package_change(self):
        """Test that changing quantity per package recalculates unit cost."""

        supplier = Supplier.objects.create(name="Bulk Supplier", supplier_type=Supplier.ONLINE)
        item = InventoryItem.objects.create(
            name="Cable Ties",
            description="4-inch cable ties",
            reorder_quantity=1,
            current_stock=100,
            minimum_stock=25,
        )

        item_supplier = ItemSupplier.objects.create(
            item=item,
            supplier=supplier,
            supplier_sku="CT-100",
            quantity_per_package=100,
            package_cost=Decimal("15.00"),
        )

        # Initial: $15.00 / 100 = $0.15 per unit
        assert item_supplier.unit_cost == Decimal("0.15")

        # Change to 200 per package with same package cost
        item_supplier.quantity_per_package = 200
        item_supplier.save()

        # New unit cost: $15.00 / 200 = $0.075 per unit
        assert item_supplier.unit_cost == Decimal("0.075")
