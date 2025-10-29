"""
Simple test for suppliers array functionality without factory complications.
"""

from decimal import Decimal

from django.test import TestCase
from inventory.models import InventoryItem, ItemSupplier
from inventory.serializers import InventoryItemSerializer
from inventory.tests.factories import CategoryFactory, LocationFactory, SupplierFactory


class TestSimpleSuppliersArray(TestCase):
    """Simple test for suppliers array in API responses."""

    def setUp(self):
        """Set up test data."""
        self.category = CategoryFactory()
        self.location = LocationFactory()

    def test_suppliers_array_basic_functionality(self):
        """Test basic suppliers array functionality."""
        # Create suppliers
        supplier1 = SupplierFactory(name="Primary Supplier")
        supplier2 = SupplierFactory(name="Secondary Supplier")

        # Create item directly
        item = InventoryItem.objects.create(
            name="Test Item",
            description="Test Description",
            category=self.category,
            location=self.location,
            reorder_quantity=10,
            current_stock=5,
            minimum_stock=2,
        )

        # Create supplier relationships directly
        ItemSupplier.objects.create(
            item=item,
            supplier=supplier1,
            supplier_sku="PRIMARY-123",
            supplier_url="https://primary.com/item",
            unit_cost=Decimal("2.00"),
            package_cost=Decimal("24.00"),
            quantity_per_package=12,
            average_lead_time=15,
            is_primary=True,
            is_active=True,
        )

        ItemSupplier.objects.create(
            item=item,
            supplier=supplier2,
            supplier_sku="SECONDARY-456",
            supplier_url="https://secondary.com/item",
            unit_cost=Decimal("1.80"),
            package_cost=Decimal("36.00"),
            quantity_per_package=20,
            average_lead_time=21,
            is_primary=False,
            is_active=True,
        )

        # Test serialization
        serializer = InventoryItemSerializer(item)
        data = serializer.data

        # Check suppliers array exists and has correct count
        self.assertIn("suppliers", data)
        self.assertIsInstance(data["suppliers"], list)
        self.assertEqual(len(data["suppliers"]), 2)

        # Check primary supplier data
        primary_supplier = next(s for s in data["suppliers"] if s["is_primary"])
        self.assertEqual(primary_supplier["supplier_name"], "Primary Supplier")
        self.assertEqual(primary_supplier["supplier_sku"], "PRIMARY-123")
        self.assertEqual(primary_supplier["supplier_url"], "https://primary.com/item")
        self.assertEqual(primary_supplier["unit_cost"], "2.00")
        self.assertEqual(primary_supplier["package_cost"], "24.00")
        self.assertEqual(primary_supplier["quantity_per_package"], 12)
        self.assertEqual(primary_supplier["average_lead_time"], 15)
        self.assertTrue(primary_supplier["is_primary"])
        self.assertTrue(primary_supplier["is_active"])

        # Check secondary supplier data
        secondary_supplier = next(s for s in data["suppliers"] if not s["is_primary"])
        self.assertEqual(secondary_supplier["supplier_name"], "Secondary Supplier")
        self.assertEqual(secondary_supplier["supplier_sku"], "SECONDARY-456")
        self.assertEqual(secondary_supplier["unit_cost"], "1.80")
        self.assertFalse(secondary_supplier["is_primary"])
        self.assertTrue(secondary_supplier["is_active"])

        # Check backward compatibility - primary supplier fields should still exist
        self.assertEqual(data["supplier_name"], "Primary Supplier")
        self.assertEqual(data["supplier_sku"], "PRIMARY-123")
        self.assertEqual(data["supplier_url"], "https://primary.com/item")

    def test_empty_suppliers_array(self):
        """Test item with no suppliers."""
        # Create item with no suppliers
        item = InventoryItem.objects.create(
            name="No Supplier Item",
            description="Test item with no suppliers",
            category=self.category,
            location=self.location,
            reorder_quantity=10,
            current_stock=5,
            minimum_stock=2,
        )

        serializer = InventoryItemSerializer(item)
        data = serializer.data

        # Should have empty suppliers array
        self.assertIn("suppliers", data)
        self.assertIsInstance(data["suppliers"], list)
        self.assertEqual(len(data["suppliers"]), 0)

        # Legacy primary supplier fields should be None/empty
        self.assertIsNone(data.get("supplier_name"))

    def test_dimensional_data_in_suppliers(self):
        """Test that dimensional data is included in suppliers array."""
        supplier = SupplierFactory(name="Dimensional Supplier")

        item = InventoryItem.objects.create(
            name="Dimensional Item",
            description="Item with dimensions",
            category=self.category,
            location=self.location,
            reorder_quantity=10,
            current_stock=5,
            minimum_stock=2,
        )

        ItemSupplier.objects.create(
            item=item,
            supplier=supplier,
            supplier_sku="DIM-123",
            unit_cost=Decimal("5.00"),
            package_cost=Decimal("50.00"),
            quantity_per_package=10,
            average_lead_time=14,
            package_height=Decimal("8.00"),
            package_width=Decimal("6.00"),
            package_length=Decimal("12.00"),
            package_weight=Decimal("3.000"),
            is_primary=True,
            is_active=True,
        )

        serializer = InventoryItemSerializer(item)
        data = serializer.data

        supplier_data = data["suppliers"][0]

        # Check dimensional data
        self.assertEqual(supplier_data["package_height"], "8.00")
        self.assertEqual(supplier_data["package_width"], "6.00")
        self.assertEqual(supplier_data["package_length"], "12.00")
        self.assertEqual(supplier_data["package_weight"], "3.000")

        # Check calculated fields
        self.assertEqual(supplier_data["package_volume"], "576.00")  # 8 * 6 * 12
        self.assertIn("package_dimensions_display", supplier_data)
        self.assertIn("unit_weight", supplier_data)

    def test_hazmat_data_with_suppliers(self):
        """Test that hazmat data works with suppliers array."""
        supplier = SupplierFactory(name="Hazmat Supplier")

        item = InventoryItem.objects.create(
            name="Hazardous Item",
            description="Hazardous material",
            category=self.category,
            location=self.location,
            reorder_quantity=5,
            current_stock=2,
            minimum_stock=1,
            is_hazardous=True,
            msds_url="https://example.com/msds/chemical.pdf",
            nfpa_health_hazard=3,
            nfpa_fire_hazard=2,
            nfpa_instability_hazard=1,
            nfpa_special_hazards="COR",
        )

        ItemSupplier.objects.create(
            item=item,
            supplier=supplier,
            supplier_sku="HAZ-789",
            unit_cost=Decimal("10.00"),
            package_cost=Decimal("100.00"),
            quantity_per_package=10,
            average_lead_time=7,
            is_primary=True,
            is_active=True,
        )

        serializer = InventoryItemSerializer(item)
        data = serializer.data

        # Check hazmat data is present
        self.assertTrue(data["is_hazardous"])
        self.assertEqual(data["msds_url"], "https://example.com/msds/chemical.pdf")
        self.assertEqual(data["nfpa_health_hazard"], 3)
        self.assertEqual(data["nfpa_fire_hazard"], 2)
        self.assertEqual(data["nfpa_instability_hazard"], 1)
        self.assertEqual(data["nfpa_special_hazards"], "COR")

        # Check suppliers array is still present
        self.assertEqual(len(data["suppliers"]), 1)
        self.assertEqual(data["suppliers"][0]["supplier_name"], "Hazmat Supplier")
