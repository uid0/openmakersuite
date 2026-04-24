"""
Signal handlers for reorder queue app.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import ReorderRequest
from .tasks import send_reorder_request_notification_email, trigger_reorder_request_webhook


@receiver(post_save, sender=ReorderRequest)
def handle_reorder_request_created(sender, instance, created, **kwargs):
    """
    Fan out notifications when a new reorder request is created.

    Currently:
      - Triggers any configured outbound webhooks.
      - Sends a Postmark email to SIG contacts / admins (see notifications.py).

    Args:
        sender: The model class (ReorderRequest)
        instance: The actual ReorderRequest instance
        created: Boolean indicating if this is a new record
        **kwargs: Additional arguments
    """
    if created:
        trigger_reorder_request_webhook.delay(instance.id)
        send_reorder_request_notification_email.delay(instance.id)
