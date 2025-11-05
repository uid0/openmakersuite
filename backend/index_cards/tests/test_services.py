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
