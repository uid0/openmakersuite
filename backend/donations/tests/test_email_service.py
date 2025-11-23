"""
Tests for donation email service.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from donations.services.email_service import DonationEmailService
from donations.tests.factories import (
    DispositionFactory,
    DonationFactory,
    DonationItemFactory,
    TaxReceiptFactory,
)


@pytest.mark.unit
class TestDonationEmailService:
    """Tests for DonationEmailService."""

    @patch("donations.services.email_service.EmailMessage")
    def test_send_tax_receipt_email_success(self, mock_email_class, db):
        """Test sending tax receipt email successfully."""
        donation = DonationFactory(
            donor_email="donor@example.com",
            donor_name="Test Donor",
            estimated_value=Decimal("100.00"),
        )
        tax_receipt = TaxReceiptFactory(donation=donation)

        mock_email = MagicMock()
        mock_email_class.return_value = mock_email

        result = DonationEmailService.send_tax_receipt_email(donation, tax_receipt)

        assert result is True
        mock_email.send.assert_called_once()

    def test_send_tax_receipt_email_no_donor_email(self, db):
        """Test that email is not sent when donor has no email."""
        donation = DonationFactory(donor_email="")
        tax_receipt = TaxReceiptFactory(donation=donation)

        result = DonationEmailService.send_tax_receipt_email(donation, tax_receipt)

        assert result is False

    @patch("donations.services.email_service.EmailMessage")
    def test_send_quarterly_update_success(self, mock_email_class, db):
        """Test sending quarterly update email successfully."""
        donation = DonationFactory(donor_email="donor@example.com", donor_name="Test Donor")
        item = DonationItemFactory(donation=donation)
        DispositionFactory(donation_item=item, disposition_type="kept")

        mock_email = MagicMock()
        mock_email_class.return_value = mock_email

        result = DonationEmailService.send_quarterly_update(donation)

        assert result is True
        mock_email.send.assert_called_once()

    def test_send_quarterly_update_no_donor_email(self, db):
        """Test that quarterly update is not sent when donor has no email."""
        donation = DonationFactory(donor_email="")

        result = DonationEmailService.send_quarterly_update(donation)

        assert result is False
