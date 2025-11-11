"""
Brother QL-820nwb label generator for assets.

The Brother QL-820nwb uses 62mm (2.44 inch) wide labels.
This generator creates PDF labels optimized for this printer.
"""

from __future__ import annotations

from io import BytesIO
from typing import Optional

from django.conf import settings

from reportlab.lib import colors
from reportlab.lib.units import inch, mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from inventory.models import Asset


class BrotherLabelRenderer:
    """Render asset labels for Brother QL-820nwb printer (62mm labels)."""

    # Brother QL-820nwb uses 62mm wide labels
    # The DK-2251 roll is continuous, so we match the printable height to 62mm
    # for a square label that trims right after the content.
    # Use inches for better printer compatibility (62mm = 2.44 inches)
    LABEL_WIDTH = 2.44 * inch  # 62mm = 2.44 inches
    LABEL_HEIGHT = 2.44 * inch  # Match the 62mm label roll height as well

    # Printer DPI - Brother QL-820NWB supports 300 x 600 dpi
    # Use 300 DPI for width (horizontal) to match printer resolution
    PRINTER_DPI = 300

    # Margins and spacing - increased for better readability
    MARGIN = 6 * mm  # Increased margin for better spacing
    TOP_PADDING = 2 * mm
    QR_SIZE = 30 * mm  # Increased QR code size for better scanning
    TEXT_AREA_WIDTH = LABEL_WIDTH - (2 * MARGIN) - QR_SIZE - (3 * mm)

    def __init__(self, base_url: Optional[str] = None) -> None:
        """Initialize the label renderer.

        Args:
            base_url: Base URL for QR code links (defaults to settings)
        """
        self.base_url = base_url or getattr(settings, "FRONTEND_URL", "http://localhost:3000")

    def render_label(self, asset: Asset) -> bytes:
        """Generate a single label PDF for an asset.

        Args:
            asset: The asset to generate a label for

        Returns:
            PDF content as bytes
        """
        buffer = BytesIO()
        # Set page size in points (1/72 inch) and specify DPI
        # Convert inches to points: 1 inch = 72 points
        page_width_pt = self.LABEL_WIDTH
        page_height_pt = self.LABEL_HEIGHT
        pdf_canvas = canvas.Canvas(
            buffer,
            pagesize=(page_width_pt, page_height_pt),
            pageCompression=0,  # Disable compression for better compatibility
        )
        # Set metadata to help printer understand the document
        pdf_canvas.setTitle(f"Label: {asset.name}")
        pdf_canvas.setSubject("Brother QL-820NWB Label")

        self._draw_label(pdf_canvas, asset)
        pdf_canvas.save()
        buffer.seek(0)

        return buffer.getvalue()

    def render_batch(self, assets: list[Asset]) -> bytes:
        """Generate labels for multiple assets.

        Args:
            assets: List of assets to generate labels for

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
        pdf_canvas.setTitle("Brother QL-820NWB Labels")
        pdf_canvas.setSubject("Asset Labels Batch")

        for asset in assets:
            self._draw_label(pdf_canvas, asset)
            pdf_canvas.showPage()

        pdf_canvas.save()
        buffer.seek(0)

        return buffer.getvalue()

    def _draw_label(self, pdf_canvas: canvas.Canvas, asset: Asset) -> None:
        """Draw a single label on the canvas.

        Args:
            pdf_canvas: ReportLab canvas to draw on
            asset: Asset to create label for
        """
        # Ensure QR code exists
        if not asset.qr_code:
            from ..services.qr_code_service import QRCodeService

            service = QRCodeService(include_logo=False)  # No logo on labels
            service.generate_for_asset(asset)
            asset.refresh_from_db()

        # Draw QR code on the left side
        if asset.qr_code:
            try:
                qr_image = ImageReader(asset.qr_code)
                # Position QR code on the left
                qr_x = self.MARGIN
                qr_y = self.LABEL_HEIGHT - self.MARGIN - self.TOP_PADDING - self.QR_SIZE
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
                    self.MARGIN,
                    self.LABEL_HEIGHT - self.MARGIN - self.TOP_PADDING - self.QR_SIZE,
                    self.QR_SIZE,
                    self.QR_SIZE,
                    fill=1,
                )

        # Text area on the right side
        text_x = self.MARGIN + self.QR_SIZE + (3 * mm)
        text_y = self.LABEL_HEIGHT - self.MARGIN - self.TOP_PADDING

        # Asset name (largest text) - increased font size
        font_size_name = 14  # Increased from 10
        pdf_canvas.setFont("Helvetica-Bold", font_size_name)
        pdf_canvas.setFillColor(colors.black)
        name_lines = self._wrap_text(
            asset.name, self.TEXT_AREA_WIDTH, "Helvetica-Bold", font_size_name
        )
        line_height = font_size_name + 2
        for i, line in enumerate(name_lines[:2]):  # Max 2 lines for name
            pdf_canvas.drawString(text_x, text_y - (i * line_height), line)

        # Asset tag - increased font size
        if asset.asset_tag:
            font_size_tag = 10  # Increased from 8
            pdf_canvas.setFont("Helvetica", font_size_tag)
            pdf_canvas.setFillColor(colors.darkgrey)
            tag_y = text_y - (len(name_lines[:2]) * line_height) - 6
            pdf_canvas.drawString(text_x, tag_y, f"Tag: {asset.asset_tag}")

        # Status indicator - increased font size
        status_y = (
            tag_y - 12 if asset.asset_tag else text_y - (len(name_lines[:2]) * line_height) - 6
        )
        font_size_status = 9  # Increased from 7
        pdf_canvas.setFont("Helvetica", font_size_status)
        status_color = colors.green if asset.status == Asset.ACTIVE else colors.orange
        pdf_canvas.setFillColor(status_color)
        pdf_canvas.drawString(text_x, status_y, asset.get_status_display())

        # Location (if available) - increased font size
        if asset.location:
            location_y = status_y - 12
            font_size_location = 9  # Increased from 7
            pdf_canvas.setFont("Helvetica", font_size_location)
            pdf_canvas.setFillColor(colors.black)
            location_text = f"Loc: {asset.location.name[:20]}"
            pdf_canvas.drawString(text_x, location_y, location_text)

    def _wrap_text(self, text: str, max_width: float, font_name: str, font_size: int) -> list[str]:
        """Wrap text to fit within a given width.

        Args:
            text: Text to wrap
            max_width: Maximum width in points
            font_name: Font name
            font_size: Font size in points

        Returns:
            List of text lines
        """
        words = text.split()
        lines = []
        current_line = ""

        for word in words:
            test_line = f"{current_line} {word}".strip()
            # Approximate width: roughly 0.6 * font_size per character
            test_width = len(test_line) * font_size * 0.6

            if test_width <= max_width:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                current_line = word

        if current_line:
            lines.append(current_line)

        return lines
