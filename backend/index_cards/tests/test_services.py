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
