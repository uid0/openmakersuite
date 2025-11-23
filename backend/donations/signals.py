"""
Signals for donations app.

Handles automatic tax receipt generation when donations are reviewed.
"""

from __future__ import annotations

import logging

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from donations.models import Donation, TaxReceipt
from donations.services.email_service import DonationEmailService
from donations.services.tax_receipt_generator import TaxReceiptPDFGenerator

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=Donation)
def donation_pre_save(sender, instance: Donation, **kwargs):
    """Track status changes before saving."""
    if instance.pk:
        try:
            old_instance = Donation.objects.get(pk=instance.pk)
            instance._old_status = old_instance.status
        except Donation.DoesNotExist:
            instance._old_status = None
    else:
        instance._old_status = None


@receiver(post_save, sender=Donation)
def donation_post_save(sender, instance: Donation, created: bool, **kwargs):
    """Generate tax receipt when donation status changes to 'reviewed'."""
    # Only generate receipt if status changed to 'reviewed'
    old_status = getattr(instance, "_old_status", None)
    if instance.status == Donation.REVIEWED and old_status != Donation.REVIEWED:
        # Check if receipt already exists
        if instance.tax_receipts.exists():
            logger.info(
                f"Tax receipt already exists for donation {instance.id}, skipping generation"
            )
            return

        try:
            # Generate tax receipt
            tax_receipt = TaxReceipt.objects.create(
                donation=instance,
                issued_by=instance.reviewed_by,
                is_copy=False,
            )

            # Generate PDF
            generator = TaxReceiptPDFGenerator(is_copy=False)
            pdf_path = generator.generate_and_save(
                instance, tax_receipt, signature_user=instance.reviewed_by
            )

            # Update tax receipt with PDF path
            tax_receipt.pdf_file = pdf_path
            tax_receipt.save()

            # Update donation
            instance.tax_receipt_issued = True
            instance.tax_receipt_number = str(tax_receipt.serial_number)
            instance.tax_receipt_issued_at = tax_receipt.issued_date
            instance.save(
                update_fields=[
                    "tax_receipt_issued",
                    "tax_receipt_number",
                    "tax_receipt_issued_at",
                ]
            )

            # Send email to donor
            if instance.donor_email:
                try:
                    # Get full path for email attachment
                    from django.core.files.storage import default_storage

                    if default_storage.exists(pdf_path):
                        full_path = default_storage.path(pdf_path)
                        DonationEmailService.send_tax_receipt_email(
                            instance, tax_receipt, pdf_path=full_path
                        )
                except Exception as e:
                    logger.error(f"Failed to send tax receipt email: {e}")

            logger.info(
                f"Tax receipt {tax_receipt.serial_number} generated for donation {instance.id}"
            )

        except Exception as e:
            logger.error(f"Failed to generate tax receipt for donation {instance.id}: {e}")
