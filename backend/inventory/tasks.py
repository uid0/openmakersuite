"""
Celery tasks for inventory management.
"""
from celery import shared_task
from django.apps import apps


@shared_task
def generate_qr_code(item_id):
    """
    Asynchronous task to generate QR code for an item.

    Args:
        item_id: UUID string of the inventory item
    """
    from .utils.qr_generator import save_qr_code_to_item

    InventoryItem = apps.get_model('inventory', 'InventoryItem')

    try:
        item = InventoryItem.objects.get(id=item_id)
        save_qr_code_to_item(item)
        return f"QR code generated for {item.name}"
    except InventoryItem.DoesNotExist:
        return f"Item {item_id} not found"


@shared_task
def generate_index_card(item_id):
    """
    Asynchronous task to generate index card PDF for an item.

    Args:
        item_id: UUID string of the inventory item
    """
    from .utils.pdf_generator import generate_item_card

    InventoryItem = apps.get_model('inventory', 'InventoryItem')

    try:
        item = InventoryItem.objects.get(id=item_id)
        pdf_buffer = generate_item_card(item)
        # You could save this to a file storage or send via email
        return f"Index card generated for {item.name}"
    except InventoryItem.DoesNotExist:
        return f"Item {item_id} not found"


@shared_task
def update_average_lead_times():
    """
    Periodic task to update average lead times based on historical data.
    """
    from django.db.models import Avg
    from datetime import timedelta
    from django.utils import timezone

    ReorderRequest = apps.get_model('reorder_queue', 'ReorderRequest')
    InventoryItem = apps.get_model('inventory', 'InventoryItem')

    # Calculate lead times for completed orders in the last 6 months
    six_months_ago = timezone.now() - timedelta(days=180)

    items = InventoryItem.objects.all()
    updated_count = 0

    for item in items:
        # Get completed reorders for this item
        completed_reorders = ReorderRequest.objects.filter(
            item=item,
            status='received',
            ordered_at__isnull=False,
            actual_delivery__isnull=False,
            ordered_at__gte=six_months_ago
        )

        if completed_reorders.exists():
            # Calculate average lead time in days
            total_days = 0
            count = 0

            for reorder in completed_reorders:
                lead_time = (reorder.actual_delivery - reorder.ordered_at.date()).days
                if lead_time > 0:
                    total_days += lead_time
                    count += 1

            if count > 0:
                item.average_lead_time = total_days // count
                item.save(update_fields=['average_lead_time'])
                updated_count += 1

    return f"Updated lead times for {updated_count} items"
