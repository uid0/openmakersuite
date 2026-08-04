"""
Email service for donation-related communications.

Handles sending tax receipts and quarterly updates to donors.
"""

from __future__ import annotations

import logging
from typing import Optional

from django.conf import settings
from django.core.mail import EmailMessage

from donations.models import Donation, TaxReceipt
from resilience.email import send_breakered_email

logger = logging.getLogger(__name__)


class DonationEmailService:
    """Service for sending donation-related emails."""

    @staticmethod
    def send_tax_receipt_email(
        donation: Donation, tax_receipt: TaxReceipt, pdf_path: Optional[str] = None
    ) -> bool:
        """Send tax receipt email to donor.

        Args:
            donation: The donation
            tax_receipt: The tax receipt
            pdf_path: Path to PDF file to attach (optional)

        Returns:
            True if email sent successfully, False otherwise
        """
        if not donation.donor_email:
            logger.warning(
                f"Cannot send tax receipt email for donation {donation.id}: no donor email"
            )
            return False

        try:
            site_name = getattr(settings, "SITE_NAME", "Makerspace")
            from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@makerspace.org")

            subject = f"Tax Receipt for Your Donation - {site_name}"

            # Create email message
            email = EmailMessage(
                subject=subject,
                body=_get_tax_receipt_email_body(donation, tax_receipt),
                from_email=from_email,
                to=[donation.donor_email],
            )

            # Attach PDF if provided
            if pdf_path:
                try:
                    with open(pdf_path, "rb") as pdf_file:
                        email.attach(
                            f"tax_receipt_{tax_receipt.serial_number}.pdf",
                            pdf_file.read(),
                            "application/pdf",
                        )
                except Exception as e:
                    logger.error(f"Could not attach PDF to email: {e}")

            send_breakered_email(email)
            logger.info(
                f"Tax receipt email sent to {donation.donor_email} for donation {donation.id}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to send tax receipt email: {e}")
            return False

    @staticmethod
    def send_quarterly_update(donation: Donation) -> bool:
        """Send quarterly update email to donor.

        Args:
            donation: The donation

        Returns:
            True if email sent successfully, False otherwise
        """
        if not donation.donor_email:
            logger.warning(
                f"Cannot send quarterly update for donation {donation.id}: no donor email"
            )
            return False

        try:
            site_name = getattr(settings, "SITE_NAME", "Makerspace")
            from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@makerspace.org")

            subject = f"Quarterly Update on Your Donation - {site_name}"

            # Generate update content
            body = _get_quarterly_update_email_body(donation)

            # Create email message
            email = EmailMessage(
                subject=subject,
                body=body,
                from_email=from_email,
                to=[donation.donor_email],
            )

            send_breakered_email(email)
            logger.info(
                f"Quarterly update email sent to {donation.donor_email} for donation {donation.id}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to send quarterly update email: {e}")
            return False


def _get_tax_receipt_email_body(donation: Donation, tax_receipt: TaxReceipt) -> str:
    """Generate email body for tax receipt."""
    site_name = getattr(settings, "SITE_NAME", "Makerspace")

    body = f"""Dear {donation.donor_name},

Thank you for your generous donation to {site_name}!

We have issued a tax receipt for your donation, which is attached to this email.

Donation Details:
- Date: {donation.date_received.strftime('%B %d, %Y')}
- Receipt Serial Number: {tax_receipt.serial_number}
- Estimated Value: ${donation.estimated_value:,.2f} (if applicable)

This receipt can be used for tax deduction purposes. Please consult with your tax advisor regarding the deductibility of your contribution.

If you have any questions, please don't hesitate to contact us.

Thank you again for your support!

Best regards,
{site_name}
"""
    return body


def _get_quarterly_update_email_body(donation: Donation) -> str:
    """Generate email body for quarterly update."""
    site_name = getattr(settings, "SITE_NAME", "Makerspace")

    # Get donation status summary
    items = donation.items.all()
    total_items = items.count()
    kept_items = sum(
        1 for item in items if item.dispositions.filter(disposition_type="kept").exists()
    )
    sold_items = sum(
        1
        for item in items
        if item.dispositions.filter(disposition_type__in=["sold", "auctioned"]).exists()
    )
    disposed_items = sum(
        1
        for item in items
        if item.dispositions.filter(disposition_type__in=["disposed", "recycled"]).exists()
    )

    body = f"""Dear {donation.donor_name},

This is a quarterly update on the status of your donation to {site_name}.

Donation Information:
- Donation Number: {donation.donation_number}
- Date Received: {donation.date_received.strftime('%B %d, %Y')}
- Total Items: {total_items}

Current Status:
- Items Kept for Use: {kept_items}
- Items Sold/Auctioned: {sold_items}
- Items Disposed/Recycled: {disposed_items}

Your generous donation continues to support our makerspace community. Items that were kept are being used by our members for various projects, and proceeds from sold items help fund our operations.

We greatly appreciate your contribution and the positive impact it has on our community.

If you have any questions, please don't hesitate to contact us.

Thank you again for your support!

Best regards,
{site_name}
"""
    return body
