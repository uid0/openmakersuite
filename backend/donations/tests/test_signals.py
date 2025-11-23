"""
Tests for donation signals.
"""

from unittest.mock import MagicMock, patch

import pytest

from donations.models import Donation
from donations.tests.factories import DonationFactory, TaxReceiptFactory
from forgekey.tests.factories import UserFactory


@pytest.mark.unit
class TestDonationSignals:
    """Tests for donation signals."""

    @patch("donations.signals.TaxReceiptPDFGenerator")
    @patch("donations.signals.DonationEmailService.send_tax_receipt_email")
    def test_tax_receipt_generated_on_reviewed_status(
        self, mock_send_email, mock_generator_class, db
    ):
        """Test that tax receipt is generated when donation status changes to reviewed."""
        user = UserFactory()
        donation = DonationFactory(status=Donation.PENDING, reviewed_by=user)

        # Mock PDF generator
        mock_generator = MagicMock()
        mock_generator.generate_and_save.return_value = "test/path.pdf"
        mock_generator_class.return_value = mock_generator

        # Change status to reviewed
        donation.status = Donation.REVIEWED
        donation.save()

        # Check that tax receipt was created
        assert donation.tax_receipts.exists()
        tax_receipt = donation.tax_receipts.first()
        assert tax_receipt is not None
        assert donation.tax_receipt_issued is True
        assert donation.tax_receipt_number == str(tax_receipt.serial_number)

    def test_tax_receipt_not_generated_if_already_exists(self, db):
        """Test that tax receipt is not generated if one already exists."""
        donation = DonationFactory(status=Donation.PENDING)
        TaxReceiptFactory(donation=donation)

        # Change status to reviewed
        donation.status = Donation.REVIEWED
        donation.save()

        # Should still only have one receipt
        assert donation.tax_receipts.count() == 1

    def test_tax_receipt_not_generated_for_other_status_changes(self, db):
        """Test that tax receipt is not generated for other status changes."""
        donation = DonationFactory(status=Donation.PENDING)

        # Change to processing (not reviewed)
        donation.status = Donation.PROCESSING
        donation.save()

        # No receipt should be created
        assert not donation.tax_receipts.exists()
