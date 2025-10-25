"""
Celery tasks for inventory management.
"""

from django.apps import apps
from django.core.files.base import ContentFile

import requests
from celery import shared_task


@shared_task
def download_image_from_url(item_id, image_url):
    """
    Asynchronous task to download image from URL for an item.

    Args:
        item_id: UUID string of the inventory item
        image_url: URL to download image from
    """
    InventoryItem = apps.get_model("inventory", "InventoryItem")

    try:
        item = InventoryItem.objects.get(id=item_id)

        # Don't download if image already exists
        if item.image:
            return f"Image already exists for {item.name}"

        # Download the image
        response = requests.get(image_url, timeout=30)
        response.raise_for_status()

        # Determine file extension from content type or URL
        content_type = response.headers.get("content-type", "")
        if "webp" in content_type:
            ext = "webp"
        elif "png" in content_type:
            ext = "png"
        elif "jpeg" in content_type or "jpg" in content_type:
            ext = "jpg"
        else:
            # Try to get from URL
            ext = image_url.split(".")[-1].split("?")[0].lower()
            if ext not in ["jpg", "jpeg", "png", "webp"]:
                ext = "jpg"  # default

        # Save the downloaded image
        image_content = ContentFile(response.content)
        item.image.save(
            f"{item.sku or item.id}.{ext}", image_content, save=True  # This will save the item
        )

        return f"Image downloaded for {item.name} from {image_url}"

    except InventoryItem.DoesNotExist:
        return f"Item {item_id} not found"
    except requests.RequestException as e:
        return f"Failed to download image for item {item_id}: {str(e)}"
    except Exception as e:
        return f"Error processing image for item {item_id}: {str(e)}"


@shared_task
def generate_qr_code(item_id):
    """
    Asynchronous task to generate QR code for an item.

    Args:
        item_id: UUID string of the inventory item
    """
    from .utils.qr_generator import save_qr_code_to_item

    InventoryItem = apps.get_model("inventory", "InventoryItem")

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

    InventoryItem = apps.get_model("inventory", "InventoryItem")

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

    Updates ItemSupplier average_lead_time based on completed reorders.
    """
    from datetime import timedelta

    from django.utils import timezone

    ReorderRequest = apps.get_model("reorder_queue", "ReorderRequest")
    ItemSupplier = apps.get_model("inventory", "ItemSupplier")

    # Calculate lead times for completed orders in the last 6 months
    six_months_ago = timezone.now() - timedelta(days=180)

    item_suppliers = ItemSupplier.objects.filter(is_active=True)
    updated_count = 0

    for item_supplier in item_suppliers:
        # Get completed reorders for this item from this supplier
        # Note: ReorderRequest would need a supplier field to track which supplier was used
        # For now, we'll update based on all reorders for the item
        completed_reorders = ReorderRequest.objects.filter(
            item=item_supplier.item,
            status="received",
            ordered_at__isnull=False,
            actual_delivery__isnull=False,
            ordered_at__gte=six_months_ago,
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
                item_supplier.average_lead_time = total_days // count
                item_supplier.save(update_fields=["average_lead_time"])
                updated_count += 1

    return f"Updated lead times for {updated_count} items"
