"""
Tests for QR code and PDF generation utilities.
"""

from io import BytesIO


import pytest
from PIL import Image

from inventory.tests.factories import InventoryItemFactory
from inventory.utils.pdf_generator import generate_bulk_cards, generate_item_card
from inventory.utils.qr_generator import generate_qr_code_image, save_qr_code_to_item


@pytest.mark.unit
class TestQRCodeGeneration:
    """Tests for QR code generation utilities."""

    def test_generate_qr_code_image(self):
        """Test generating a QR code image."""
        item_id = "test-uuid-1234"
        qr_buffer = generate_qr_code_image(item_id)

        assert isinstance(qr_buffer, BytesIO)
        assert qr_buffer.tell() == 0  # Buffer is at start

        # Verify it's a valid image
        img = Image.open(qr_buffer)
        assert img.format == "PNG"
        assert img.size[0] > 0
        assert img.size[1] > 0

    def test_generate_qr_code_with_custom_url(self):
        """Test generating QR code with custom base URL."""
        item_id = "test-uuid-1234"
        base_url = "https://custom.example.com"
        qr_buffer = generate_qr_code_image(item_id, base_url=base_url)

        assert isinstance(qr_buffer, BytesIO)

    def test_save_qr_code_to_item(self):
        """Test saving QR code to an inventory item."""
        item = InventoryItemFactory()
        assert not item.qr_code  # No QR code initially

        save_qr_code_to_item(item)

        # Verify QR code was saved
        item.refresh_from_db()
        assert item.qr_code
        assert item.qr_code.name.startswith("inventory/qrcodes/qr_")


@pytest.mark.unit
class TestPDFGeneration:
    """Tests for PDF generation utilities."""

    def test_generate_item_card(self):
        """Test generating a 5x3 horizontal index card PDF."""
        item = InventoryItemFactory(
            name="Test Widget",
            description="A test item for PDF generation",
            reorder_quantity=25,
            location="Shelf A",
        )

        pdf_buffer = generate_item_card(item)

        assert isinstance(pdf_buffer, BytesIO)
        assert pdf_buffer.tell() == 0  # Buffer is at start
        assert len(pdf_buffer.getvalue()) > 0  # PDF has content

    def test_generate_item_card_with_image(self, sample_image):
        """Test generating card for item with image."""
        item = InventoryItemFactory(image=sample_image)

        pdf_buffer = generate_item_card(item)

        assert isinstance(pdf_buffer, BytesIO)
        assert len(pdf_buffer.getvalue()) > 0

    def test_generate_item_card_without_image(self):
        """Test generating card for item without image."""
        item = InventoryItemFactory(image=None)

        pdf_buffer = generate_item_card(item)

        assert isinstance(pdf_buffer, BytesIO)
        assert len(pdf_buffer.getvalue()) > 0

    def test_generate_item_card_with_qr_code(self):
        """Test generating card for item with QR code."""
        item = InventoryItemFactory()
        save_qr_code_to_item(item)

        pdf_buffer = generate_item_card(item)

        assert isinstance(pdf_buffer, BytesIO)
        assert len(pdf_buffer.getvalue()) > 0

    def test_generate_bulk_cards(self):
        """Test generating multiple cards on letter pages."""
        items = InventoryItemFactory.create_batch(5)

        pdf_buffer = generate_bulk_cards(items)

        assert isinstance(pdf_buffer, BytesIO)
        # Just verify that the PDF was created successfully
        assert len(pdf_buffer.getvalue()) > 0

    def test_generate_card_handles_long_text(self):
        """Test PDF generation handles long text gracefully."""
        item = InventoryItemFactory(
            name="A" * 100,  # Very long name
            description="B" * 500,  # Very long description
            location="C" * 100,  # Very long location
        )

        pdf_buffer = generate_item_card(item)

        assert isinstance(pdf_buffer, BytesIO)
        assert len(pdf_buffer.getvalue()) > 0
