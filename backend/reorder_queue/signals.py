"""
Signal handlers for reorder queue app.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import ReorderRequest
from .tasks import trigger_reorder_request_webhook


@receiver(post_save, sender=ReorderRequest)
def handle_reorder_request_created(sender, instance, created, **kwargs):
    """
    Trigger webhook notification when a new reorder request is created.

    Args:
        sender: The model class (ReorderRequest)
        instance: The actual ReorderRequest instance
        created: Boolean indicating if this is a new record
        **kwargs: Additional arguments
    """
    if created:
        # Trigger webhook asynchronously via Celery
        trigger_reorder_request_webhook.delay(instance.id)
