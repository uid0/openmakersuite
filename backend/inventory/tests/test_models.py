"""
Unit tests for inventory models.
"""

from decimal import Decimal

import pytest

from inventory.models import Category, InventoryItem, ItemSupplier, Supplier, UsageLog
from inventory.tests.factories import (
    CategoryFactory,
    InventoryItemFactory,
    ItemSupplierFactory,
    SupplierFactory,
    UsageLogFactory,
)

pytestmark = pytest.mark.django_db


@pytest.mark.unit
class TestSupplierModel:
    """Tests for the Supplier model."""

    def test_supplier_creation(self):
        """Test creating a supplier instance."""
        supplier = SupplierFactory(supplier_type="local")
        assert supplier.name is not None
        assert supplier.supplier_type == "local"
        assert str(supplier) == supplier.name

    def test_supplier_ordering(self):
        """Test suppliers are ordered by name."""
        # Create suppliers to test ordering
        SupplierFactory()
        SupplierFactory()

        suppliers = Supplier.objects.all().order_by("name")
        # Verify ordering works (first supplier should come before second alphabetically)
        assert suppliers[0].name < suppliers[1].name


@pytest.mark.unit
class TestCategoryModel:
    """Tests for the Category model."""

    def test_category_creation(self):
        """Test creating a category instance."""
        category = CategoryFactory()
        assert category.name is not None
        assert category.slug == category.name.lower().replace(" ", "-")

    def test_category_slug_auto_generation(self):
        """Test slug is auto-generated from name."""
        category = Category.objects.create(name="Power Tools")
        assert category.slug == "power-tools"

    def test_category_with_parent(self):
        """Test creating a subcategory."""
        parent = CategoryFactory()
        child = CategoryFactory(parent=parent)

        assert child.parent == parent
        assert child in parent.children.all()


@pytest.mark.unit
class TestInventoryItemModel:
    """Tests for the InventoryItem model."""

    def test_item_creation(self):
        """Test creating an inventory item."""
        item = InventoryItemFactory(
            name="Test Widget", reorder_quantity=25, current_stock=50, minimum_stock=10
        )
        assert item.name == "Test Widget"
        assert item.reorder_quantity == 25
        assert str(item) == "Test Widget"

    def test_needs_reorder_true(self):
        """Test needs_reorder returns True when stock is low."""
        item = InventoryItemFactory(current_stock=5, minimum_stock=10)
        assert item.needs_reorder is True

    def test_needs_reorder_false(self):
        """Test needs_reorder returns False when stock is adequate."""
        item = InventoryItemFactory(current_stock=50, minimum_stock=10)
        assert item.needs_reorder is False

    def test_needs_reorder_at_minimum(self):
        """Test needs_reorder returns True when stock equals minimum."""
        item = InventoryItemFactory(current_stock=10, minimum_stock=10)
        assert item.needs_reorder is True

    def test_total_value_calculation(self):
        """Test total_value property calculates correctly."""
        item = InventoryItemFactory(current_stock=10, unit_cost=Decimal("25.50"))
        assert item.total_value == Decimal("255.00")

    def test_total_value_without_cost(self):
        """Test total_value returns 0 when unit_cost is None."""
        item = InventoryItemFactory(unit_cost=None)
        assert item.total_value == 0

    def test_item_with_category(self):
        """Test item with category relationship."""
        category = CategoryFactory()
        item = InventoryItemFactory(category=category)

        assert item.category == category
        assert item in category.items.all()

    def test_item_with_supplier(self):
        """Test item with supplier relationship."""
        supplier = SupplierFactory()
        item = InventoryItemFactory(supplier=supplier)

        assert item.supplier == supplier
        assert item in supplier.items.all()


@pytest.mark.unit
class TestUsageLogModel:
    """Tests for the UsageLog model."""

    def test_usage_log_creation(self):
        """Test creating a usage log entry."""
        item = InventoryItemFactory()
        log = UsageLogFactory(item=item, quantity_used=5, notes="Test usage")

        assert log.item == item
        assert log.quantity_used == 5
        assert log.notes == "Test usage"
        assert str(log).startswith(item.name)

    def test_usage_log_ordering(self):
        """Test usage logs are ordered by date descending."""
        item = InventoryItemFactory()
        log1 = UsageLogFactory(item=item)
        log2 = UsageLogFactory(item=item)

        logs = UsageLog.objects.all()
        assert logs[0] == log2  # Most recent first
        assert logs[1] == log1


@pytest.mark.django_db
class TestItemSupplierModel:
    """Tests for the ItemSupplier through model."""

    def test_package_cost_auto_calculation(self):
        """Unit cost should be auto-calculated from package cost and quantity."""

        supplier = Supplier.objects.create(name="Bulk Supplies", supplier_type=Supplier.LOCAL)
        item = InventoryItem.objects.create(
            name="Zip Ties",
            description="Standard 4-inch zip ties",
            reorder_quantity=10,
            current_stock=100,
            minimum_stock=20,
        )

        # Test new preferred workflow: enter package cost, unit cost is calculated
        item_supplier = ItemSupplier.objects.create(
            item=item,
            supplier=supplier,
            supplier_sku="ZT-100",
            quantity_per_package=200,
            package_cost=Decimal("10.00"),
        )

        # Unit cost should be auto-calculated: $10.00 / 200 = $0.05
        assert item_supplier.unit_cost == Decimal("0.05")

    def test_backward_compatibility_unit_cost_entry(self):
        """Legacy unit cost entry should still work and calculate package cost."""

        supplier = Supplier.objects.create(name="Local Shop", supplier_type=Supplier.LOCAL)
        item = InventoryItem.objects.create(
            name="Painter's Tape",
            description="Blue painter's tape roll",
            reorder_quantity=5,
            current_stock=20,
            minimum_stock=5,
        )

        # Test backward compatibility: enter unit cost, package cost is calculated
        item_supplier = ItemSupplier.objects.create(
            item=item,
            supplier=supplier,
            supplier_sku="PT-50",
            quantity_per_package=50,
            unit_cost=Decimal("0.16"),
        )

        # Package cost should be auto-calculated: $0.16 * 50 = $8.00
        assert item_supplier.package_cost == Decimal("8.00")

    def test_no_pricing_data(self):
        """When no pricing is provided, both fields should be None."""

        supplier = Supplier.objects.create(name="Local Shop", supplier_type=Supplier.LOCAL)
        item = InventoryItem.objects.create(
            name="Painter's Tape",
            description="Blue painter's tape roll",
            reorder_quantity=5,
            current_stock=20,
            minimum_stock=5,
        )

        item_supplier = ItemSupplier.objects.create(
            item=item,
            supplier=supplier,
            supplier_sku="PT-50",
            quantity_per_package=50,
            unit_cost=None,
            package_cost=None,
        )

        assert item_supplier.package_cost is None
        assert item_supplier.unit_cost is None

    def test_supplier_upc_fields_capture_package_and_unit_codes(self):
        """Each supplier relationship should store UPCs for package and individual units."""

        supplier = Supplier.objects.create(
            name="Warehouse Supplier", supplier_type=Supplier.NATIONAL
        )
        item = InventoryItem.objects.create(
            name="Nitrile Gloves",
            description="Disposable nitrile gloves",
            reorder_quantity=4,
            current_stock=80,
            minimum_stock=20,
        )

        item_supplier = ItemSupplier.objects.create(
            item=item,
            supplier=supplier,
            supplier_sku="GLV-200",
            package_upc="012345678905",
            unit_upc="00012345678905",
            quantity_per_package=200,
            unit_cost=Decimal("0.08"),
        )

        assert item_supplier.package_upc == "012345678905"
        assert item_supplier.unit_upc == "00012345678905"
        # sanity check that package cost remains accurate with UPC metadata present
        assert item_supplier.package_cost == Decimal("16.00")


class TestCaseBasedReordering:
    """Test case-based reordering functionality."""

    def test_current_cases_calculation(self):
        """Test that current_cases property calculates correctly."""
        # Create item with case-based reordering enabled
        item = InventoryItemFactory(
            current_stock=120,
            use_case_based_reorder=True,
            minimum_cases=2,
            reorder_cases=5,
        )

        # Create supplier with quantity per package
        supplier = SupplierFactory()
        ItemSupplierFactory(
            item=item,
            supplier=supplier,
            quantity_per_package=24,  # 24 units per case
            is_primary=True,
        )

        # 120 units / 24 units per case = 5 cases
        assert item.current_cases == 5.0

    def test_current_cases_with_partial_case(self):
        """Test current_cases with partial cases."""
        item = InventoryItemFactory(current_stock=100, use_case_based_reorder=True)

        supplier = SupplierFactory()
        ItemSupplierFactory(item=item, supplier=supplier, quantity_per_package=24, is_primary=True)

        # 100 units / 24 units per case = 4.167 cases
        expected_cases = 100 / 24
        assert abs(item.current_cases - expected_cases) < 0.001

    def test_current_cases_disabled(self):
        """Test current_cases returns 0 when case-based reordering is disabled."""
        item = InventoryItemFactory(current_stock=120, use_case_based_reorder=False)

        assert item.current_cases == 0

    def test_needs_reorder_case_based(self):
        """Test needs_reorder logic for case-based items."""
        # Create item with 2 cases minimum, currently has 3 cases
        item = InventoryItemFactory(
            current_stock=72,  # 3 cases * 24 units per case
            use_case_based_reorder=True,
            minimum_cases=2,
            minimum_stock=10,  # This should be ignored for case-based items
        )

        supplier = SupplierFactory()
        ItemSupplierFactory(item=item, supplier=supplier, quantity_per_package=24, is_primary=True)

        # Should not need reorder (3 cases > 2 minimum cases)
        assert not item.needs_reorder

    def test_needs_reorder_case_based_low_stock(self):
        """Test needs_reorder when case-based item is below minimum cases."""
        # Create item with 2 cases minimum, currently has 1.5 cases
        item = InventoryItemFactory(
            current_stock=36,  # 1.5 cases * 24 units per case
            use_case_based_reorder=True,
            minimum_cases=2,
        )

        supplier = SupplierFactory()
        ItemSupplierFactory(item=item, supplier=supplier, quantity_per_package=24, is_primary=True)

        # Should need reorder (1.5 cases <= 2 minimum cases)
        assert item.needs_reorder

    def test_needs_reorder_case_based_exactly_at_minimum(self):
        """Test needs_reorder when exactly at minimum cases."""
        # Create item with 2 cases minimum, currently has exactly 2 cases
        item = InventoryItemFactory(
            current_stock=48,  # 2 cases * 24 units per case
            use_case_based_reorder=True,
            minimum_cases=2,
        )

        supplier = SupplierFactory()
        ItemSupplierFactory(item=item, supplier=supplier, quantity_per_package=24, is_primary=True)

        # Should need reorder (2 cases <= 2 minimum cases)
        assert item.needs_reorder

    def test_needs_reorder_traditional_mode_unchanged(self):
        """Test that traditional reordering logic still works when case-based is disabled."""
        item = InventoryItemFactory(current_stock=5, minimum_stock=10, use_case_based_reorder=False)

        # Should need reorder using traditional logic
        assert item.needs_reorder

    def test_case_based_reorder_scenario_trashbags(self):
        """Test the trashbag scenario: reorder when 1 box is left."""
        # Trashbags come 100 per box, want to reorder when 1 box is left
        trashbags = InventoryItemFactory(
            name="Heavy Duty Trash Bags",
            current_stock=150,  # 1.5 boxes
            use_case_based_reorder=True,
            minimum_cases=1,  # Reorder when 1 box is left
            reorder_cases=3,  # Reorder 3 boxes at a time
        )

        supplier = SupplierFactory()
        ItemSupplierFactory(
            item=trashbags,
            supplier=supplier,
            quantity_per_package=100,  # 100 bags per box
            is_primary=True,
        )

        # Should not need reorder yet (1.5 boxes > 1 minimum box)
        assert not trashbags.needs_reorder

        # Reduce stock to exactly 1 box
        trashbags.current_stock = 100
        trashbags.save()

        # Should need reorder now (1 box <= 1 minimum box)
        assert trashbags.needs_reorder

    def test_case_based_reorder_scenario_paper_towels(self):
        """Test the paper towel scenario: 6 per case, reorder when opening second-to-last case."""
        # Paper towels come 6 per case, want to reorder when opening second-to-last case (1 case left)
        paper_towels = InventoryItemFactory(
            name="Paper Towels - Bathroom",
            current_stock=12,  # 2 cases
            use_case_based_reorder=True,
            minimum_cases=1,  # Reorder when 1 case is left
            reorder_cases=2,  # Reorder 2 cases at a time
        )

        supplier = SupplierFactory()
        ItemSupplierFactory(
            item=paper_towels,
            supplier=supplier,
            quantity_per_package=6,  # 6 rolls per case
            is_primary=True,
        )

        # Should not need reorder yet (2 cases > 1 minimum case)
        assert not paper_towels.needs_reorder

        # Reduce stock to 1 case
        paper_towels.current_stock = 6
        paper_towels.save()

        # Should need reorder now (1 case <= 1 minimum case)
        assert paper_towels.needs_reorder
