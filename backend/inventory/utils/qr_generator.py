"""
QR code generation utilities.
"""

from io import BytesIO

import qrcode
from django.conf import settings
from django.core.files import File


def generate_qr_code_image(item_id, base_url=None):
    """
    Generate a QR code for an inventory item.

    Args:
        item_id: UUID of the inventory item
        base_url: Base URL for the scan page (defaults to settings)

    Returns:
        BytesIO object containing the QR code image
    """
    if base_url is None:
        # Use environment variable or default
        base_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")

    # Create URL that will be encoded in QR code
    scan_url = f"{base_url}/scan/{item_id}"

    # Generate QR code
    qr = qrcode.QRCode(
        version=1,  # Controls size (1 is smallest)
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(scan_url)
    qr.make(fit=True)

    # Create image
    img = qr.make_image(fill_color="black", back_color="white")

    # Save to BytesIO
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    return buffer


def save_qr_code_to_item(item):
    """
    Generate and save QR code to an inventory item.

    Args:
        item: InventoryItem instance
    """
    qr_buffer = generate_qr_code_image(str(item.id))

    # Save to item's qr_code field
    filename = f"qr_{item.id}.png"
    item.qr_code.save(filename, File(qr_buffer), save=True)

    return item
