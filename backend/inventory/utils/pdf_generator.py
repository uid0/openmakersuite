"""
PDF generation utilities for 3x5 inch index cards.
"""

import textwrap
from io import BytesIO

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph


def generate_item_card(item):
    """
    Generate a 3x5 inch index card PDF for an inventory item.

    Args:
        item: InventoryItem instance

    Returns:
        BytesIO object containing the PDF
    """
    # Create buffer
    buffer = BytesIO()

    # 3x5 inch card dimensions
    card_width = 5 * inch
    card_height = 3 * inch

    # Create PDF with custom page size
    c = canvas.Canvas(buffer, pagesize=(card_width, card_height))

    # Margins
    margin = 0.2 * inch
    content_width = card_width - (2 * margin)
    content_height = card_height - (2 * margin)

    # Draw border (optional, for cutting guide)
    c.setStrokeColor(colors.lightgrey)
    c.rect(margin / 2, margin / 2, card_width - margin, card_height - margin)

    # Layout positions
    left_section_width = content_width * 0.65  # 65% for text
    right_section_width = content_width * 0.35  # 35% for image and QR

    # Current Y position
    y_pos = card_height - margin

    # Title
    c.setFont("Helvetica-Bold", 14)
    title = item.name[:40]  # Truncate if too long
    c.drawString(margin, y_pos, title)
    y_pos -= 0.25 * inch

    # Description (wrapped text)
    c.setFont("Helvetica", 9)
    description_text = item.description[:150]  # Truncate if too long
    wrapped_lines = textwrap.wrap(description_text, width=45)

    for line in wrapped_lines[:3]:  # Max 3 lines
        c.drawString(margin, y_pos, line)
        y_pos -= 0.15 * inch

    # Reorder quantity (highlighted)
    y_pos -= 0.1 * inch
    c.setFont("Helvetica-Bold", 11)
    c.setFillColor(colors.red)
    c.drawString(margin, y_pos, f"Reorder Qty: {item.reorder_quantity}")
    c.setFillColor(colors.black)
    y_pos -= 0.2 * inch

    # Additional info
    c.setFont("Helvetica", 8)
    info_items = []

    if item.location:
        location_name = getattr(item.location, "name", str(item.location))
        if location_name:
            info_items.append(f"Location: {location_name[:20]}")
    if item.sku:
        info_items.append(f"SKU: {item.sku}")
    if item.supplier:
        info_items.append(f"Supplier: {item.supplier.name[:20]}")

    for info in info_items:
        c.drawString(margin, y_pos, info)
        y_pos -= 0.12 * inch

    # Right side - Product image (if available)
    image_x = margin + left_section_width + 0.1 * inch
    image_y = card_height - margin - 1.2 * inch
    image_size = 1.2 * inch

    if item.image:
        try:
            # Try to load and draw the image
            img = ImageReader(item.image.path)
            c.drawImage(
                img,
                image_x,
                image_y,
                width=image_size,
                height=image_size,
                preserveAspectRatio=True,
                mask="auto",
            )
        except Exception as e:
            # If image fails, draw a placeholder
            c.setStrokeColor(colors.grey)
            c.rect(image_x, image_y, image_size, image_size)
            c.setFont("Helvetica", 6)
            c.drawString(image_x + 0.1 * inch, image_y + 0.6 * inch, "No Image")

    # QR Code (below image)
    qr_y = image_y - image_size - 0.1 * inch
    qr_size = image_size

    if item.qr_code:
        try:
            qr_img = ImageReader(item.qr_code.path)
            c.drawImage(
                qr_img, image_x, qr_y, width=qr_size, height=qr_size, preserveAspectRatio=True
            )
        except Exception as e:
            # Draw placeholder for QR
            c.setStrokeColor(colors.grey)
            c.rect(image_x, qr_y, qr_size, qr_size)
            c.setFont("Helvetica", 6)
            c.drawString(image_x + 0.2 * inch, qr_y + 0.6 * inch, "Scan to")
            c.drawString(image_x + 0.2 * inch, qr_y + 0.45 * inch, "Reorder")

    # Footer with ID
    c.setFont("Helvetica", 6)
    c.setFillColor(colors.grey)
    c.drawString(margin, margin * 0.5, f"ID: {str(item.id)[:13]}")

    # Finalize PDF
    c.showPage()
    c.save()

    buffer.seek(0)
    return buffer


def generate_bulk_cards(items):
    """
    Generate multiple index cards on standard letter-size pages.

    Args:
        items: QuerySet or list of InventoryItem instances

    Returns:
        BytesIO object containing the PDF with multiple cards
    """
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)

    card_width = 5 * inch
    card_height = 3 * inch

    # Calculate positions for cards on letter page (8.5 x 11)
    page_width, page_height = letter
    cards_per_row = int(page_width / card_width)
    cards_per_col = int(page_height / card_height)

    card_count = 0

    for item in items:
        # Calculate position
        row = (card_count // cards_per_row) % cards_per_col
        col = card_count % cards_per_row

        # If we've filled a page, create a new one
        if row == 0 and col == 0 and card_count > 0:
            c.showPage()

        x_offset = col * card_width
        y_offset = page_height - (row + 1) * card_height

        # Save state and translate
        c.saveState()
        c.translate(x_offset, y_offset)

        # Draw card content (simplified version)
        # You could reuse the generate_item_card logic here
        margin = 0.2 * inch

        # Border
        c.setStrokeColor(colors.lightgrey)
        c.rect(0, 0, card_width, card_height)

        # Content (simplified)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(margin, card_height - margin - 0.2 * inch, item.name[:30])

        c.setFont("Helvetica-Bold", 10)
        c.setFillColor(colors.red)
        c.drawString(margin, margin + 0.3 * inch, f"Reorder: {item.reorder_quantity}")
        c.setFillColor(colors.black)

        c.restoreState()

        card_count += 1

    c.showPage()
    c.save()

    buffer.seek(0)
    return buffer
