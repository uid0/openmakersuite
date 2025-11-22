"""
Celery tasks for donations app.

Handles quarterly email updates to donors.
"""

import logging

from celery import shared_task

from donations.models import Donation
from donations.services.email_service import DonationEmailService

logger = logging.getLogger(__name__)


@shared_task
def send_quarterly_donor_updates():
    """
    Send quarterly update emails to all donors who have received tax receipts.

    This task should be scheduled to run quarterly (e.g., on Jan 1, Apr 1, Jul 1, Oct 1).
    """
    logger.info("Starting quarterly donor update email task")

    # Get all donations with issued tax receipts
    donations = Donation.objects.filter(tax_receipt_issued=True).select_related()

    sent_count = 0
    failed_count = 0

    for donation in donations:
        try:
            # Only send if donor has email
            if donation.donor_email:
                success = DonationEmailService.send_quarterly_update(donation)
                if success:
                    sent_count += 1
                else:
                    failed_count += 1
        except Exception as e:
            logger.error(f"Failed to send quarterly update for donation {donation.id}: {e}")
            failed_count += 1

    logger.info(f"Quarterly donor update task completed: {sent_count} sent, {failed_count} failed")

    return {
        "sent": sent_count,
        "failed": failed_count,
        "total": donations.count(),
    }
