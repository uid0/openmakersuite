"""
2x2 inch QR code label generator for donation items.

Generates PDF labels optimized for 2x2" square labels.
"""

from __future__ import annotations

from io import BytesIO
from typing import Optional

from django.conf import settings

from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from ..models import DonationItem


class DonationLabelRenderer:
    """Render 2x2 inch QR code labels for donation items."""

    # 2x2 inch labels
    LABEL_WIDTH = 2.0 * inch
    LABEL_HEIGHT = 2.0 * inch

    # Printer DPI - standard is 300 DPI
    PRINTER_DPI = 300

    # Margins and spacing
    MARGIN = 0.1 * inch
    QR_SIZE = 1.4 * inch  # Large QR code for easy scanning
    TEXT_AREA_HEIGHT = 0.4 * inch

    def __init__(self, base_url: Optional[str] = None) -> None:
        """Initialize the label renderer.

        Args:
            base_url: Base URL for QR code links (defaults to settings)
        """
        self.base_url = base_url or getattr(settings, "FRONTEND_URL", "http://localhost:3000")

    def render_label(self, donation_item: DonationItem) -> bytes:
        """Generate a single label PDF for a donation item.

        Args:
            donation_item: The donation item to generate a label for

        Returns:
            PDF content as bytes
        """
        buffer = BytesIO()
        # Set page size in points (1/72 inch)
        page_width_pt = self.LABEL_WIDTH
        page_height_pt = self.LABEL_HEIGHT
        pdf_canvas = canvas.Canvas(
            buffer,
            pagesize=(page_width_pt, page_height_pt),
            pageCompression=0,  # Disable compression for better compatibility
        )
        # Set metadata
        pdf_canvas.setTitle(f"Donation Label: {donation_item.name}")
        pdf_canvas.setSubject("Donation Item QR Code Label")

        self._draw_label(pdf_canvas, donation_item)
        pdf_canvas.save()
        buffer.seek(0)

        return buffer.getvalue()

    def render_batch(self, donation_items: list[DonationItem]) -> bytes:
        """Generate labels for multiple donation items.

        Args:
            donation_items: List of donation items to generate labels for

        Returns:
            PDF content as bytes with one label per page
        """
        buffer = BytesIO()
        page_width_pt = self.LABEL_WIDTH
        page_height_pt = self.LABEL_HEIGHT
        pdf_canvas = canvas.Canvas(
            buffer,
            pagesize=(page_width_pt, page_height_pt),
            pageCompression=0,
        )
        pdf_canvas.setTitle("Donation Item Labels")
        pdf_canvas.setSubject("Donation QR Code Labels Batch")

        for item in donation_items:
            self._draw_label(pdf_canvas, item)
            pdf_canvas.showPage()

        pdf_canvas.save()
        buffer.seek(0)

        return buffer.getvalue()

    def _draw_label(self, pdf_canvas: canvas.Canvas, donation_item: DonationItem) -> None:
        """Draw a single label on the canvas.

        Args:
            pdf_canvas: ReportLab canvas to draw on
            donation_item: Donation item to create label for
        """
        # Ensure QR code exists
        if not donation_item.qr_code:
            from ..services.qr_code_service import DonationItemQRCodeService

            service = DonationItemQRCodeService(include_logo=False)  # No logo on labels
            service.generate_for_donation_item(donation_item, require_access_code=False)
            donation_item.refresh_from_db()

        # Draw QR code centered
        qr_x = (self.LABEL_WIDTH - self.QR_SIZE) / 2
        qr_y = self.LABEL_HEIGHT - self.MARGIN - self.QR_SIZE - self.TEXT_AREA_HEIGHT

        if donation_item.qr_code:
            try:
                qr_image = ImageReader(donation_item.qr_code)
                pdf_canvas.drawImage(
                    qr_image,
                    qr_x,
                    qr_y,
                    width=self.QR_SIZE,
                    height=self.QR_SIZE,
                    preserveAspectRatio=True,
                )
            except Exception:
                # If QR code can't be loaded, draw a placeholder
                pdf_canvas.setFillColor(colors.grey)
                pdf_canvas.rect(
                    qr_x,
                    qr_y,
                    self.QR_SIZE,
                    self.QR_SIZE,
                    fill=1,
                )

        # Text area below QR code
        text_y = (
            self.LABEL_HEIGHT - self.MARGIN - self.QR_SIZE - self.TEXT_AREA_HEIGHT - 0.05 * inch
        )
        text_x = self.MARGIN

        # Donation number (small, top of text area)
        font_size_small = 8
        pdf_canvas.setFont("Helvetica", font_size_small)
        pdf_canvas.setFillColor(colors.darkgrey)
        donation_num = donation_item.donation.donation_number
        pdf_canvas.drawString(text_x, text_y, donation_num)

        # Item name (larger, below donation number)
        font_size_medium = 10
        pdf_canvas.setFont("Helvetica-Bold", font_size_medium)
        pdf_canvas.setFillColor(colors.black)
        item_name = donation_item.name
        # Truncate if too long
        max_width = self.LABEL_WIDTH - (2 * self.MARGIN)
        item_name = self._truncate_text(item_name, max_width, font_size_medium)
        text_y -= font_size_small + 2
        pdf_canvas.drawString(text_x, text_y, item_name)

    def _truncate_text(self, text: str, max_width: float, font_size: int) -> str:
        """Truncate text to fit within a given width.

        Args:
            text: Text to truncate
            max_width: Maximum width in points
            font_size: Font size in points

        Returns:
            Truncated text with ellipsis if needed
        """
        # Approximate width: roughly 0.6 * font_size per character
        max_chars = int(max_width / (font_size * 0.6))
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3] + "..."
