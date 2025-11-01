"""
Tests for Celery tasks.
"""

from unittest.mock import patch

import pytest
from inventory.tasks import (
    generate_index_card,
    generate_qr_code,
    update_average_lead_times,
)
from inventory.tests.factories import InventoryItemFactory
from reorder_queue.tests.factories import ReorderRequestFactory


@pytest.mark.unit
class TestInventoryTasks:
    """Tests for inventory Celery tasks."""

    @patch("inventory.utils.qr_generator.save_qr_code_to_item")
    def test_generate_qr_code_task(self, mock_save_qr):
        """Test QR code generation task."""
        item = InventoryItemFactory()

        result = generate_qr_code(str(item.id))

        assert f"QR code generated for {item.name}" in result
        mock_save_qr.assert_called_once_with(item)

    @patch("inventory.utils.qr_generator.save_qr_code_to_item")
    def test_generate_qr_code_task_item_not_found(self, mock_save_qr):
        """Test QR code generation task with non-existent item."""
        import uuid

        fake_id = str(uuid.uuid4())  # Valid UUID format but doesn't exist in DB

        result = generate_qr_code(fake_id)

        assert f"Item {fake_id} not found" in result
        mock_save_qr.assert_not_called()

    @patch("inventory.utils.pdf_generator.generate_item_card")
    def test_generate_index_card_task(self, mock_generate_pdf):
        """Test index card generation task."""
        item = InventoryItemFactory()
        from io import BytesIO

        mock_generate_pdf.return_value = BytesIO(b"fake pdf")

        result = generate_index_card(str(item.id))

        assert f"Index card generated for {item.name}" in result
        mock_generate_pdf.assert_called_once_with(item)

    @patch("inventory.utils.pdf_generator.generate_item_card")
    def test_generate_index_card_task_item_not_found(self, mock_generate_pdf):
        """Test index card generation task with non-existent item."""
        import uuid

        fake_id = str(uuid.uuid4())  # Valid UUID format but doesn't exist in DB

        result = generate_index_card(fake_id)

        assert f"Item {fake_id} not found" in result
        mock_generate_pdf.assert_not_called()

    def test_update_average_lead_times_task(self):
        """Test updating average lead times based on historical data."""
        from datetime import timedelta

        from django.utils import timezone

        # Create item
        item = InventoryItemFactory(average_lead_time=7)

        # Create completed reorder requests with known lead times
        ordered_date = timezone.now() - timedelta(days=20)
        delivery_date = (ordered_date + timedelta(days=10)).date()

        ReorderRequestFactory(
            item=item, status="received", ordered_at=ordered_date, actual_delivery=delivery_date
        )

        # Second request with different lead time
        ordered_date2 = timezone.now() - timedelta(days=15)
        delivery_date2 = (ordered_date2 + timedelta(days=8)).date()

        ReorderRequestFactory(
            item=item, status="received", ordered_at=ordered_date2, actual_delivery=delivery_date2
        )

        result = update_average_lead_times()

        # Verify item's average lead time was updated
        item.refresh_from_db()
        # Average of 10 and 8 = 9 days
        assert item.average_lead_time == 9
        assert "Updated lead times for 1 items" in result

    def test_update_average_lead_times_no_data(self):
        """Test task when no completed reorders exist."""
        InventoryItemFactory()  # Item with no reorder history

        result = update_average_lead_times()

        assert "Updated lead times for 0 items" in result

    def test_update_average_lead_times_ignores_pending(self):
        """Test task ignores non-received reorder requests."""
        item = InventoryItemFactory(average_lead_time=7)

        # Pending request should be ignored
        ReorderRequestFactory(item=item, status="pending")

        # Approved request should be ignored
        ReorderRequestFactory(item=item, status="approved")

        update_average_lead_times()

        # Lead time should not change
        item.refresh_from_db()
        assert item.average_lead_time == 7
