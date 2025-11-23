"""
Tests for donation Celery tasks.
"""

from unittest.mock import patch

import pytest

from donations.tasks import send_quarterly_donor_updates
from donations.tests.factories import DonationFactory


@pytest.mark.unit
class TestDonationTasks:
    """Tests for donation Celery tasks."""

    @patch("donations.tasks.DonationEmailService.send_quarterly_update")
    def test_send_quarterly_donor_updates(self, mock_send_update, db):
        """Test quarterly donor update task."""
        # Create donations with tax receipts issued
        DonationFactory(
            tax_receipt_issued=True,
            donor_email="donor1@example.com",
        )
        DonationFactory(
            tax_receipt_issued=True,
            donor_email="donor2@example.com",
        )
        # Donation without email (should be skipped)
        DonationFactory(
            tax_receipt_issued=True,
            donor_email="",
        )
        # Donation without tax receipt (should be skipped)
        DonationFactory(tax_receipt_issued=False, donor_email="donor4@example.com")

        mock_send_update.return_value = True

        result = send_quarterly_donor_updates()

        assert result["sent"] == 2
        assert result["failed"] == 0
        assert result["total"] == 3
        assert mock_send_update.call_count == 2

    @patch("donations.tasks.DonationEmailService.send_quarterly_update")
    def test_send_quarterly_donor_updates_with_failures(self, mock_send_update, db):
        """Test quarterly donor update task with some failures."""
        DonationFactory(
            tax_receipt_issued=True,
            donor_email="donor1@example.com",
        )
        DonationFactory(
            tax_receipt_issued=True,
            donor_email="donor2@example.com",
        )

        # First call succeeds, second fails
        mock_send_update.side_effect = [True, False]

        result = send_quarterly_donor_updates()

        assert result["sent"] == 1
        assert result["failed"] == 1
        assert result["total"] == 2
