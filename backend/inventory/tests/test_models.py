"""
Unit tests for inventory models.
"""
import pytest
from decimal import Decimal
from inventory.models import Supplier, Category, InventoryItem, UsageLog
from inventory.tests.factories import (
    SupplierFactory, CategoryFactory, InventoryItemFactory, UsageLogFactory
)


@pytest.mark.unit
class TestSupplierModel:
    """Tests for the Supplier model."""

    def test_supplier_creation(self):
        """Test creating a supplier instance."""
        supplier = SupplierFactory(
            name='Test Supplier',
            supplier_type='amazon'
        )
        assert supplier.name == 'Test Supplier'
        assert supplier.supplier_type == 'amazon'
        assert str(supplier) == 'Test Supplier'

    def test_supplier_ordering(self):
        """Test suppliers are ordered by name."""
        SupplierFactory(name='Zebra Supply')
        SupplierFactory(name='Alpha Supply')

        suppliers = Supplier.objects.all()
        assert suppliers[0].name == 'Alpha Supply'
        assert suppliers[1].name == 'Zebra Supply'


@pytest.mark.unit
class TestCategoryModel:
    """Tests for the Category model."""

    def test_category_creation(self):
        """Test creating a category instance."""
        category = CategoryFactory(name='Electronics')
        assert category.name == 'Electronics'
        assert category.slug == 'electronics'

    def test_category_slug_auto_generation(self):
        """Test slug is auto-generated from name."""
        category = Category.objects.create(name='Power Tools')
        assert category.slug == 'power-tools'

    def test_category_with_parent(self):
        """Test creating a subcategory."""
        parent = CategoryFactory(name='Tools')
        child = CategoryFactory(name='Hand Tools', parent=parent)

        assert child.parent == parent
        assert child in parent.children.all()


@pytest.mark.unit
class TestInventoryItemModel:
    """Tests for the InventoryItem model."""

    def test_item_creation(self):
        """Test creating an inventory item."""
        item = InventoryItemFactory(
            name='Test Widget',
            reorder_quantity=25,
            current_stock=50,
            minimum_stock=10
        )
        assert item.name == 'Test Widget'
        assert item.reorder_quantity == 25
        assert str(item) == 'Test Widget'

    def test_needs_reorder_true(self):
        """Test needs_reorder returns True when stock is low."""
        item = InventoryItemFactory(
            current_stock=5,
            minimum_stock=10
        )
        assert item.needs_reorder is True

    def test_needs_reorder_false(self):
        """Test needs_reorder returns False when stock is adequate."""
        item = InventoryItemFactory(
            current_stock=50,
            minimum_stock=10
        )
        assert item.needs_reorder is False

    def test_needs_reorder_at_minimum(self):
        """Test needs_reorder returns True when stock equals minimum."""
        item = InventoryItemFactory(
            current_stock=10,
            minimum_stock=10
        )
        assert item.needs_reorder is True

    def test_total_value_calculation(self):
        """Test total_value property calculates correctly."""
        item = InventoryItemFactory(
            current_stock=10,
            unit_cost=Decimal('25.50')
        )
        assert item.total_value == Decimal('255.00')

    def test_total_value_without_cost(self):
        """Test total_value returns 0 when unit_cost is None."""
        item = InventoryItemFactory(unit_cost=None)
        assert item.total_value == 0

    def test_item_with_category(self):
        """Test item with category relationship."""
        category = CategoryFactory(name='Hardware')
        item = InventoryItemFactory(category=category)

        assert item.category == category
        assert item in category.items.all()

    def test_item_with_supplier(self):
        """Test item with supplier relationship."""
        supplier = SupplierFactory(name='Acme Corp')
        item = InventoryItemFactory(supplier=supplier)

        assert item.supplier == supplier
        assert item in supplier.items.all()


@pytest.mark.unit
class TestUsageLogModel:
    """Tests for the UsageLog model."""

    def test_usage_log_creation(self):
        """Test creating a usage log entry."""
        item = InventoryItemFactory()
        log = UsageLogFactory(
            item=item,
            quantity_used=5,
            notes='Test usage'
        )

        assert log.item == item
        assert log.quantity_used == 5
        assert log.notes == 'Test usage'
        assert str(log).startswith(item.name)

    def test_usage_log_ordering(self):
        """Test usage logs are ordered by date descending."""
        item = InventoryItemFactory()
        log1 = UsageLogFactory(item=item)
        log2 = UsageLogFactory(item=item)

        logs = UsageLog.objects.all()
        assert logs[0] == log2  # Most recent first
        assert logs[1] == log1
