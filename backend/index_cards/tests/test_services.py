"""Tests for index card rendering services."""

from __future__ import annotations

import os
import shutil
import tempfile

from django.conf import settings
from django.test import TestCase, override_settings

from index_cards.services import IndexCardRenderer
from inventory.models import InventoryItem


class IndexCardRendererTests(TestCase):
    """Verify that the renderer creates PDFs and persists them to storage."""

    def setUp(self) -> None:
        self.item = InventoryItem.objects.create(
            name="Laser Cutter Lens",
            description="High quality replacement lens for the makerspace laser cutter.",
            reorder_quantity=5,
            current_stock=2,
            minimum_stock=3,
        )

    def test_render_preview_returns_pdf_bytes(self) -> None:
        renderer = IndexCardRenderer(base_url="http://localhost:3000")
        preview_bytes = renderer.render_preview(self.item)

        self.assertTrue(preview_bytes.startswith(b"%PDF"))
        self.assertGreater(len(preview_bytes), 200)

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_render_batch_to_storage_saves_file(self) -> None:
        renderer = IndexCardRenderer(base_url="http://localhost:3000")
        generated = renderer.render_batch_to_storage([self.item], filename="test_cards.pdf")

        self.assertTrue(generated.path.endswith("test_cards.pdf"))
        self.assertTrue(os.path.exists(generated.absolute_path))

        with open(generated.absolute_path, "rb") as pdf_file:
            header = pdf_file.read(4)
        self.assertEqual(header, b"%PDF")

        # Clean up temporary media directory
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)

    def test_case_based_item_rendering(self) -> None:
        """Test that case-based items render correctly in index cards."""
        # Create a case-based item (like trashbags)
        case_item = InventoryItem.objects.create(
            name="Heavy Duty Trash Bags",
            description="33-gallon trash bags, 100 per box",
            reorder_quantity=300,  # Traditional field (will be ignored)
            current_stock=150,  # 1.5 boxes worth
            minimum_stock=100,  # Traditional field (will be ignored)
            use_case_based_reorder=True,
            minimum_cases=1,  # Reorder when 1 case left
            reorder_cases=3,  # Order 3 cases at a time
        )

        # Create a supplier with quantity per package to calculate current_cases
        from inventory.models import ItemSupplier, Supplier

        supplier = Supplier.objects.create(name="Test Supplier", supplier_type="online")
        ItemSupplier.objects.create(
            item=case_item,
            supplier=supplier,
            quantity_per_package=100,  # 100 bags per box
            unit_cost=0.46,
            is_primary=True,
        )

        renderer = IndexCardRenderer(base_url="http://localhost:3000")
        preview_bytes = renderer.render_preview(case_item)

        # Verify PDF is generated
        self.assertTrue(preview_bytes.startswith(b"%PDF"))
        self.assertGreater(len(preview_bytes), 200)

        # Note: The actual content verification would require PDF parsing,
        # but the important part is that the renderer doesn't crash with case-based items

    def test_render_item_with_top_shelf_position(self) -> None:
        """Test that items with shelf_position='top' render without errors."""
        item = InventoryItem.objects.create(
            name="Top Shelf Item",
            description="Item stored on top shelf",
            reorder_quantity=10,
            current_stock=5,
            minimum_stock=3,
            shelf_position="top",
        )

        renderer = IndexCardRenderer(base_url="http://localhost:3000")
        preview_bytes = renderer.render_preview(item)

        # Verify PDF is generated successfully
        self.assertTrue(preview_bytes.startswith(b"%PDF"))
        self.assertGreater(len(preview_bytes), 200)

    def test_render_item_with_bottom_shelf_position(self) -> None:
        """Test that items with shelf_position='bottom' render without errors."""
        item = InventoryItem.objects.create(
            name="Bottom Shelf Item",
            description="Item stored on bottom shelf",
            reorder_quantity=10,
            current_stock=5,
            minimum_stock=3,
            shelf_position="bottom",
        )

        renderer = IndexCardRenderer(base_url="http://localhost:3000")
        preview_bytes = renderer.render_preview(item)

        # Verify PDF is generated successfully
        self.assertTrue(preview_bytes.startswith(b"%PDF"))
        self.assertGreater(len(preview_bytes), 200)

    def test_render_batch_with_shelf_positions(self) -> None:
        """Test that batch rendering works with items that have shelf positions."""
        top_item = InventoryItem.objects.create(
            name="Top Shelf Item",
            description="Item on top shelf",
            reorder_quantity=10,
            current_stock=5,
            minimum_stock=3,
            shelf_position="top",
        )

        bottom_item = InventoryItem.objects.create(
            name="Bottom Shelf Item",
            description="Item on bottom shelf",
            reorder_quantity=10,
            current_stock=5,
            minimum_stock=3,
            shelf_position="bottom",
        )

        no_position_item = InventoryItem.objects.create(
            name="Regular Item",
            description="Item without shelf position",
            reorder_quantity=10,
            current_stock=5,
            minimum_stock=3,
        )

        renderer = IndexCardRenderer(base_url="http://localhost:3000")
        # render_preview expects a single item, use render_to_bytes for multiple items
        preview_bytes = renderer.render_to_bytes([top_item, bottom_item, no_position_item])

        # Verify PDF is generated successfully
        self.assertTrue(preview_bytes.startswith(b"%PDF"))
        self.assertGreater(len(preview_bytes), 500)  # Should be larger with multiple items

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_render_batch_to_storage_with_shelf_positions(self) -> None:
        """Test that batch rendering to storage works with shelf positions."""
        top_item = InventoryItem.objects.create(
            name="Top Shelf Item",
            description="Item on top shelf",
            reorder_quantity=10,
            current_stock=5,
            minimum_stock=3,
            shelf_position="top",
        )

        bottom_item = InventoryItem.objects.create(
            name="Bottom Shelf Item",
            description="Item on bottom shelf",
            reorder_quantity=10,
            current_stock=5,
            minimum_stock=3,
            shelf_position="bottom",
        )

        renderer = IndexCardRenderer(base_url="http://localhost:3000")
        generated = renderer.render_batch_to_storage(
            [top_item, bottom_item], filename="test_shelf_cards.pdf"
        )

        self.assertTrue(generated.path.endswith("test_shelf_cards.pdf"))
        self.assertTrue(os.path.exists(generated.absolute_path))

        with open(generated.absolute_path, "rb") as pdf_file:
            header = pdf_file.read(4)
        self.assertEqual(header, b"%PDF")

        # Clean up temporary media directory
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)


class ReorderAtLineTests(TestCase):
    """The card's "Reorder at:" line names the unit the item is counted in (op-es7c).

    One helper feeds both the drawn line and the shelf-arrow's height
    measurement, so these assertions pin the text for both.
    """

    def setUp(self) -> None:
        self.renderer = IndexCardRenderer(base_url="http://localhost:3000")

    def _item(self, **kwargs) -> InventoryItem:
        defaults = {
            "name": "Nitrile gloves",
            "reorder_quantity": 5,
            "current_stock": 2,
            "minimum_stock": 3,
        }
        return InventoryItem.objects.create(**{**defaults, **kwargs})

    def test_plain_item_reads_in_base_units(self) -> None:
        item = self._item(minimum_stock=3)

        self.assertEqual(self.renderer._reorder_at_line(item), "Reorder at: 3 units")

    def test_case_based_item_still_reads_in_cases(self) -> None:
        item = self._item(use_case_based_reorder=True, minimum_cases=1)

        self.assertEqual(self.renderer._reorder_at_line(item), "Reorder at: 1 case")

    def test_pack_counting_item_reads_in_its_own_packs(self) -> None:
        from inventory.models import PackagingLevel

        item = self._item(base_unit="glove", minimum_stock=2)
        case = PackagingLevel.objects.create(item=item, name="box", sort_order=0, base_units=100)
        PackagingLevel.objects.create(item=item, name="glove", sort_order=1, base_units=1)
        item.count_mode = InventoryItem.CountMode.BY_LEVEL
        item.count_level = case
        item.save(update_fields=["count_mode", "count_level"])

        self.assertEqual(self.renderer._reorder_at_line(item), "Reorder at: 2 boxes")

    def test_custom_base_unit_labels_an_each_item(self) -> None:
        """An item that opted into a named base unit gets that noun, not "unit"."""
        item = self._item(base_unit="sheet", minimum_stock=500)

        self.assertEqual(self.renderer._reorder_at_line(item), "Reorder at: 500 sheets")
