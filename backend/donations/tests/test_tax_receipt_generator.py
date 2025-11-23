"""
Tests for tax receipt PDF generator.
"""

from decimal import Decimal

import pytest

from customization.models import SiteSettings
from donations.models import Donation, TaxReceipt
from donations.services.tax_receipt_generator import TaxReceiptPDFGenerator
from donations.tests.factories import DonationFactory, DonationItemFactory, TaxReceiptFactory
from forgekey.tests.factories import UserFactory


@pytest.mark.unit
class TestTaxReceiptPDFGenerator:
    """Tests for TaxReceiptPDFGenerator."""

    def test_generate_pdf_basic(self, db):
        """Test basic PDF generation."""
        # Setup site settings
        site_settings = SiteSettings.get()
        site_settings.site_name = "Test Makerspace"
        site_settings.organization_address = "123 Test St\nTest City, ST 12345"
        site_settings.organization_ein = "12-3456789"
        site_settings.authorized_signature_name = "Test Signer"
        site_settings.authorized_signature_title = "Director"
        site_settings.save()

        # Create donation with items
        donation = DonationFactory(
            donor_name="Test Donor",
            donor_email="donor@example.com",
            donor_address="456 Donor St\nDonor City, ST 67890",
            estimated_value=Decimal("500.00"),
        )
        # Create items - use code generator for unique access codes
        from donations.models import DonationItem
        from inventory.utils.code_generator import generate_unique_code

        item1 = DonationItem.objects.create(
            donation=donation,
            name="Test Item 1",
            quantity=2,
            condition=DonationItem.GOOD,
            status=DonationItem.PENDING_REVIEW,
            access_code=generate_unique_code(DonationItem, "access_code"),
        )
        item2 = DonationItem.objects.create(
            donation=donation,
            name="Test Item 2",
            quantity=1,
            condition=DonationItem.GOOD,
            status=DonationItem.PENDING_REVIEW,
            access_code=generate_unique_code(DonationItem, "access_code"),
        )

        # Create tax receipt
        tax_receipt = TaxReceiptFactory(donation=donation)

        # Generate PDF
        generator = TaxReceiptPDFGenerator(is_copy=False)
        pdf_bytes = generator.generate_pdf(donation, tax_receipt)

        # Verify PDF was generated
        assert pdf_bytes is not None
        assert len(pdf_bytes) > 0
        assert pdf_bytes.startswith(b"%PDF")

    def test_generate_pdf_with_copy_watermark(self, db):
        """Test PDF generation with copy watermark."""
        site_settings = SiteSettings.get()
        site_settings.site_name = "Test Makerspace"
        site_settings.save()

        donation = DonationFactory()
        tax_receipt = TaxReceiptFactory(donation=donation)

        generator = TaxReceiptPDFGenerator(is_copy=True)
        pdf_bytes = generator.generate_pdf(donation, tax_receipt)

        assert pdf_bytes is not None
        assert len(pdf_bytes) > 0

    def test_generate_pdf_with_signature(self, db):
        """Test PDF generation with user signature."""
        site_settings = SiteSettings.get()
        site_settings.site_name = "Test Makerspace"
        site_settings.save()

        user = UserFactory()
        # Note: In real usage, user would have signature_image, but we'll test without it
        donation = DonationFactory()
        tax_receipt = TaxReceiptFactory(donation=donation, issued_by=user)

        generator = TaxReceiptPDFGenerator(is_copy=False)
        pdf_bytes = generator.generate_pdf(donation, tax_receipt, signature_user=user)

        assert pdf_bytes is not None
        assert len(pdf_bytes) > 0

    def test_generate_and_save(self, db, tmp_path, monkeypatch):
        """Test generating and saving PDF to storage."""
        from django.core.files.storage import default_storage
        from unittest.mock import patch, MagicMock

        site_settings = SiteSettings.get()
        site_settings.site_name = "Test Makerspace"
        site_settings.save()

        donation = DonationFactory()
        tax_receipt = TaxReceiptFactory(donation=donation)

        generator = TaxReceiptPDFGenerator(is_copy=False)

        # Mock storage
        with patch.object(default_storage, "save") as mock_save:
            mock_save.return_value = "donations/tax_receipts/test.pdf"
            with patch.object(default_storage, "exists") as mock_exists:
                mock_exists.return_value = False

                path = generator.generate_and_save(donation, tax_receipt)

                assert path is not None
                mock_save.assert_called_once()

