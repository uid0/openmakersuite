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
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from inventory.models import Asset


class BrotherLabelRenderer:
    """Render asset labels for Brother QL-820nwb printer (62mm labels)."""

    # Brother QL-820nwb uses 62mm wide labels
    # Standard continuous label height is typically 100mm or 200mm
    # We'll use 100mm (3.94 inches) for a good balance
    LABEL_WIDTH = 62 * mm  # 62mm = 2.44 inches
    LABEL_HEIGHT = 100 * mm  # 100mm = 3.94 inches

    # Margins and spacing
    MARGIN = 2 * mm
    QR_SIZE = 25 * mm  # QR code size
    TEXT_AREA_WIDTH = LABEL_WIDTH - (2 * MARGIN) - QR_SIZE - (2 * mm)

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
        pdf_canvas = canvas.Canvas(buffer, pagesize=(self.LABEL_WIDTH, self.LABEL_HEIGHT))

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
        pdf_canvas = canvas.Canvas(buffer, pagesize=(self.LABEL_WIDTH, self.LABEL_HEIGHT))

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
            from .qr_generator import save_qr_code_to_asset

            save_qr_code_to_asset(asset)
            asset.refresh_from_db()

        # Draw QR code on the left side
        if asset.qr_code:
            try:
                qr_image = ImageReader(asset.qr_code)
                # Position QR code on the left
                qr_x = self.MARGIN
                qr_y = self.LABEL_HEIGHT - self.MARGIN - self.QR_SIZE
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
                    self.LABEL_HEIGHT - self.MARGIN - self.QR_SIZE,
                    self.QR_SIZE,
                    self.QR_SIZE,
                    fill=1,
                )

        # Text area on the right side
        text_x = self.MARGIN + self.QR_SIZE + (2 * mm)
        text_y = self.LABEL_HEIGHT - self.MARGIN

        # Asset name (largest text)
        pdf_canvas.setFont("Helvetica-Bold", 10)
        pdf_canvas.setFillColor(colors.black)
        name_lines = self._wrap_text(asset.name, self.TEXT_AREA_WIDTH, "Helvetica-Bold", 10)
        for i, line in enumerate(name_lines[:2]):  # Max 2 lines for name
            pdf_canvas.drawString(text_x, text_y - (i * 12), line)

        # Asset tag
        if asset.asset_tag:
            pdf_canvas.setFont("Helvetica", 8)
            pdf_canvas.setFillColor(colors.darkgrey)
            tag_y = text_y - (len(name_lines[:2]) * 12) - 8
            pdf_canvas.drawString(text_x, tag_y, f"Tag: {asset.asset_tag}")

        # Status indicator
        status_y = tag_y - 10 if asset.asset_tag else text_y - (len(name_lines[:2]) * 12) - 8
        pdf_canvas.setFont("Helvetica", 7)
        status_color = colors.green if asset.status == Asset.ACTIVE else colors.orange
        pdf_canvas.setFillColor(status_color)
        pdf_canvas.drawString(text_x, status_y, asset.get_status_display())

        # Location (if available)
        if asset.location:
            location_y = status_y - 10
            pdf_canvas.setFont("Helvetica", 7)
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
