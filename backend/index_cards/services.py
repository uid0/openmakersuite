"""Utilities for rendering inventory index cards."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Iterable, List, Sequence

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone

import qrcode
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

from inventory.models import InventoryItem


@dataclass
class GeneratedCardFile:
    """Metadata about a generated PDF."""

    path: str
    url: str
    absolute_path: str | None


class IndexCardRenderer:
    """Render inventory items into 3x5" vertical index cards, printed 3 across horizontally."""

    PAGE_WIDTH, PAGE_HEIGHT = letter
    CARD_WIDTH = 3 * inch  # 3" wide for vertical layout
    CARD_HEIGHT = 5 * inch  # 5" tall for vertical layout
    TOP_MARGIN = 0.5 * inch
    LEFT_MARGIN = 0.75 * inch  # Left margin for first card
    HORIZONTAL_GAP = 0.25 * inch  # Space between cards when printing horizontally
    CARD_PADDING = 0.25 * inch
    IMAGE_MAX_WIDTH = 2.0 * inch  # Larger for vertical layout
    IMAGE_MAX_HEIGHT = 1.5 * inch  # Max height for vertical layout
    QR_CODE_SIZE = 1.2 * inch  # Sized for vertical layout

    CALL_TO_ACTION = "Need to Re-Order?\nScan the QR code above to notify Logistics!"

    def __init__(
        self, base_url: str | None = None, blank_cards: bool = False
    ) -> None:
        """Initialize the renderer with base URL and card type.

        Args:
            base_url: Base URL for QR codes (defaults to FRONTEND_URL setting)
            blank_cards: If True, render blank cards with only QR codes
        """
        self.base_url = base_url or getattr(
            settings, "FRONTEND_URL", "http://localhost:3000"
        )
        self.blank_cards = blank_cards
        self._title_style = ParagraphStyle(
            name="CardTitle",
            fontName="Helvetica-Bold",
            fontSize=16,
            leading=18,
            alignment=TA_LEFT,
            textColor=colors.HexColor("#1F2937"),
        )
        self._body_style = ParagraphStyle(
            name="CardBody",
            fontName="Helvetica",
            fontSize=10,
            leading=12,
            alignment=TA_LEFT,
            textColor=colors.HexColor("#111827"),
        )
        self._highlight_style = ParagraphStyle(
            name="CardHighlight",
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=14,
            alignment=TA_LEFT,
            textColor=colors.HexColor("#111827"),
        )

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------
    def render_preview(self, item: InventoryItem, blank_card: bool = False) -> bytes:
        """Render a single-item preview and return PDF bytes.

        Args:
            item: Item to render
            blank_card: If True, render blank card with only QR code
        """
        self.blank_cards = blank_card
        buffer = BytesIO()
        self._render_to_canvas([item], buffer)
        return buffer.getvalue()

    def render_batch_to_storage(
        self,
        items: Sequence[InventoryItem],
        filename: str | None = None,
        blank_cards: bool = False,
    ) -> GeneratedCardFile:
        """Render cards for a sequence of items and persist the PDF."""
        if not items:
            raise ValueError("At least one item is required to render index cards.")

        self.blank_cards = blank_cards
        normalized_name = self._normalize_filename(filename)

        # Add blank suffix to filename if blank cards
        if blank_cards:
            normalized_name = normalized_name.replace('.pdf', '_blank.pdf')

        pdf_bytes = self.render_to_bytes(items)

        storage_path = Path("index_cards") / normalized_name
        storage_str = storage_path.as_posix()

        if default_storage.exists(storage_str):
            default_storage.delete(storage_str)

        saved_path = default_storage.save(storage_str, ContentFile(pdf_bytes))
        try:
            absolute_path = default_storage.path(saved_path)
        except (NotImplementedError, AttributeError):
            absolute_path = None

        return GeneratedCardFile(
            path=saved_path,
            url=default_storage.url(saved_path),
            absolute_path=absolute_path,
        )

    def render_to_bytes(
        self, items: Sequence[InventoryItem], blank_cards: bool = False
    ) -> bytes:
        """Render cards to PDF bytes without saving to storage."""
        self.blank_cards = blank_cards
        buffer = BytesIO()
        self._render_to_canvas(items, buffer)
        return buffer.getvalue()

    def encode_preview(
        self, item: InventoryItem, blank_card: bool = False
    ) -> str:
        """Return a base64 encoded preview PDF for quick display."""
        pdf_bytes = self.render_preview(item, blank_card)
        return base64.b64encode(pdf_bytes).decode("ascii")

    # ------------------------------------------------------------------
    # Internal rendering helpers
    # ------------------------------------------------------------------
    def _render_to_canvas(
        self, items: Sequence[InventoryItem], buffer: BytesIO
    ) -> None:
        pdf_canvas = canvas.Canvas(buffer, pagesize=letter)

        for page_items in self._chunk(items, 3):  # 3 vertical cards per page
            self._draw_page(pdf_canvas, page_items)
            pdf_canvas.showPage()

        pdf_canvas.save()
        buffer.seek(0)

    def _draw_page(
        self, pdf_canvas: canvas.Canvas, items: Sequence[InventoryItem]
    ) -> None:
        # Draw 3 vertical cards horizontally across the page
        cards_per_page = 3

        # Calculate horizontal spacing to fit 3 cards across
        available_width = self.PAGE_WIDTH - 2 * self.LEFT_MARGIN
        card_spacing = (available_width - 3 * self.CARD_WIDTH) / 2

        for index, item in enumerate(items):
            # Calculate horizontal position - cards placed side by side
            x_offset = (
                self.LEFT_MARGIN + index * (self.CARD_WIDTH + card_spacing)
            )
            # Center cards vertically on page
            y_offset = (self.PAGE_HEIGHT - self.CARD_HEIGHT) / 2
            self._draw_card(pdf_canvas, item, x_offset, y_offset)

        # Add cutting marks for plain cardstock
        self._draw_cutting_marks(pdf_canvas, cards_per_page, card_spacing)

    def _draw_card(
        self,
        pdf_canvas: canvas.Canvas,
        item: InventoryItem,
        origin_x: float,
        origin_y: float,
    ) -> None:
        pdf_canvas.roundRect(
            origin_x,
            origin_y,
            self.CARD_WIDTH,
            self.CARD_HEIGHT,
            radius=12,
            stroke=1,
            fill=0,
        )

        inner_x = origin_x + self.CARD_PADDING
        inner_y = origin_y + self.CARD_PADDING
        available_width = self.CARD_WIDTH - 2 * self.CARD_PADDING

        # For blank cards, only show QR code
        if self.blank_cards:
            # QR code centered on blank card
            qr_x = origin_x + (self.CARD_WIDTH - self.QR_CODE_SIZE) / 2
            qr_y = origin_y + (self.CARD_HEIGHT - self.QR_CODE_SIZE) / 2

            # Generate QR code
            qr_buffer = self._generate_qr_code(item)
            qr_reader = ImageReader(qr_buffer)
            pdf_canvas.drawImage(
                qr_reader,
                qr_x,
                qr_y,
                width=self.QR_CODE_SIZE,
                height=self.QR_CODE_SIZE,
                preserveAspectRatio=True,
            )
        else:
            # Detailed cards with full information - vertical layout
            current_y = origin_y + self.CARD_HEIGHT - self.CARD_PADDING

            # Title - centered at top for vertical layout
            title_para = Paragraph(item.name, self._title_style)
            title_width, title_height = title_para.wrap(
                available_width, self.CARD_HEIGHT / 4
            )
            title_width = min(title_width, available_width)
            title_x = origin_x + (self.CARD_WIDTH - title_width) / 2
            title_para.drawOn(pdf_canvas, title_x, current_y - title_height)
            current_y -= title_height + 12

            # Image placement - centered below title for vertical layout
            image_drawn_height = 0
            if item.image and hasattr(item.image, "path"):
                if os.path.exists(item.image.path):
                    image_reader = ImageReader(item.image.path)
                    image_width, image_height = image_reader.getSize()
                    scale = min(
                        self.IMAGE_MAX_WIDTH / image_width,
                        self.IMAGE_MAX_HEIGHT / image_height,
                        1,
                    )
                    image_drawn_width = image_width * scale
                    image_drawn_height = image_height * scale
                    image_x = origin_x + (self.CARD_WIDTH - image_drawn_width) / 2
                    image_y = current_y - image_drawn_height
                    pdf_canvas.drawImage(
                        image_reader,
                        image_x,
                        image_y,
                        width=image_drawn_width,
                        height=image_drawn_height,
                        preserveAspectRatio=True,
                        mask="auto",
                    )
                    current_y -= image_drawn_height + 12

            # Description - full width for vertical layout
            description_para = Paragraph(item.description, self._body_style)
            max_desc_height = 0.8 * inch  # Limit description height
            _, description_height = description_para.wrap(
                available_width, max_desc_height
            )
            description_height = min(description_height, max_desc_height)
            description_para.drawOn(
                pdf_canvas, inner_x, current_y - description_height
            )
            current_y -= description_height + 15

            # Stock information - full width for vertical layout
            # Focus on static info suitable for laminated cards
            target_stock = self._calculate_desired_stock(item)
            info_lines = [
                f"Stock: {target_stock} units (Target)",
                f"Reorder Qty: {item.reorder_quantity}",
            ]

            # Add lead time if available
            if item.average_lead_time:
                info_lines.append(f"Lead Time: {item.average_lead_time} days")

            self._draw_info_lines(
                pdf_canvas, info_lines, inner_x, current_y, available_width
            )
            # Calculate actual height used by info lines
            info_lines_height = len(info_lines) * self._highlight_style.leading
            current_y -= info_lines_height + 20  # Extra spacing after info lines

            # QR code placement - centered for vertical layout, below info lines
            qr_x = origin_x + (self.CARD_WIDTH - self.QR_CODE_SIZE) / 2
            qr_y = max(
                current_y - self.QR_CODE_SIZE - 10, inner_y + 50
            )  # Reserve space for CTA with more buffer

            qr_buffer = self._generate_qr_code(item)
            qr_reader = ImageReader(qr_buffer)
            pdf_canvas.drawImage(
                qr_reader,
                qr_x,
                qr_y,
                width=self.QR_CODE_SIZE,
                height=self.QR_CODE_SIZE,
                preserveAspectRatio=True,
            )

            # Call to action - below QR code, centered with inverse text
            # Use category color if available, otherwise use default black
            if item.category and item.category.color:
                try:
                    bg_color = colors.HexColor(item.category.color)
                except (ValueError, AttributeError):
                    bg_color = colors.black
            else:
                bg_color = colors.black

            # Calculate dimensions for background box
            cta_lines = self.CALL_TO_ACTION.split("\n")
            line_height = 14  # Increased for better spacing
            padding_vertical = 8  # More vertical padding
            padding_horizontal = 6  # Horizontal padding
            box_height = len(cta_lines) * line_height + 2 * padding_vertical
            box_width = available_width - 0.1 * inch  # Wider box
            box_x = inner_x + 0.05 * inch
            box_y = qr_y - 14 - box_height + padding_vertical

            # Draw colored background box with rounded corners
            pdf_canvas.setFillColor(bg_color)
            pdf_canvas.roundRect(
                box_x,
                box_y,
                box_width,
                box_height,
                radius=6,  # Slightly larger radius
                stroke=0,
                fill=1,
            )

            # Draw white text on colored background
            pdf_canvas.setFillColor(colors.white)
            pdf_canvas.setFont("Helvetica-Bold", 9)
            cta_y = box_y + box_height - padding_vertical - 10  # Start from top of box
            for line in cta_lines:
                pdf_canvas.drawCentredString(
                    origin_x + self.CARD_WIDTH / 2, cta_y, line
                )
                cta_y -= line_height

            # Reset fill color to black for any subsequent drawing
            pdf_canvas.setFillColor(colors.black)

    def _draw_info_lines(
        self,
        pdf_canvas: canvas.Canvas,
        lines: List[str],
        origin_x: float,
        origin_y: float,
        max_width: float,
    ) -> None:
        # Ensure we don't draw below the card bottom
        card_bottom = origin_y - self.CARD_HEIGHT + 2 * self.CARD_PADDING
        if origin_y < card_bottom + 20:
            return  # Skip if too close to bottom

        text_object = pdf_canvas.beginText()
        text_object.setTextOrigin(origin_x, origin_y)
        text_object.setFont(self._highlight_style.fontName, self._highlight_style.fontSize)
        leading = self._highlight_style.leading

        for line in lines:
            if not line:
                continue
            # Check if we have enough space for this line
            if origin_y - leading < card_bottom + 10:
                break  # Stop if no space left

            wrapped = self._wrap_text(
                line, max_width, self._highlight_style.fontName, self._highlight_style.fontSize
            )
            for fragment in wrapped:
                text_object.textLine(fragment)
                origin_y -= leading
                # Safety check - don't go below card bottom
                if origin_y < card_bottom + 10:
                    break

        pdf_canvas.drawText(text_object)

    def _draw_cutting_marks(
        self, pdf_canvas: canvas.Canvas, cards_per_page: int, card_spacing: float
    ) -> None:
        """Draw cutting marks for plain cardstock."""
        # Cutting marks for 3 vertical cards placed horizontally
        pdf_canvas.setStrokeColor(colors.gray)
        pdf_canvas.setLineWidth(0.5)

        # Vertical position for cards (centered on page)
        y_center = (self.PAGE_HEIGHT - self.CARD_HEIGHT) / 2

        for i in range(cards_per_page + 1):  # Marks between and around cards
            x_pos = self.LEFT_MARGIN + i * (self.CARD_WIDTH + card_spacing)

            # Top cutting marks
            pdf_canvas.line(
                x_pos,
                y_center + self.CARD_HEIGHT + 0.125 * inch,
                x_pos,
                y_center + self.CARD_HEIGHT + 0.25 * inch,
            )
            if i < cards_per_page:
                pdf_canvas.line(
                    x_pos + self.CARD_WIDTH,
                    y_center + self.CARD_HEIGHT + 0.125 * inch,
                    x_pos + self.CARD_WIDTH,
                    y_center + self.CARD_HEIGHT + 0.25 * inch,
                )

            # Bottom cutting marks
            pdf_canvas.line(
                x_pos, y_center - 0.25 * inch, x_pos, y_center - 0.125 * inch
            )
            if i < cards_per_page:
                pdf_canvas.line(
                    x_pos + self.CARD_WIDTH,
                    y_center - 0.25 * inch,
                    x_pos + self.CARD_WIDTH,
                    y_center - 0.125 * inch,
                )

    def _wrap_text(
        self,
        text: str,
        max_width: float,
        font_name: str,
        font_size: int,
    ) -> List[str]:
        words = text.split()
        if not words:
            return []

        line = words.pop(0)
        lines: List[str] = []
        while words:
            next_word = words.pop(0)
            candidate = f"{line} {next_word}"
            if pdfmetrics.stringWidth(candidate, font_name, font_size) <= max_width:
                line = candidate
            else:
                lines.append(line)
                line = next_word
        lines.append(line)
        return lines

    def _generate_qr_code(self, item: InventoryItem) -> BytesIO:
        qr = qrcode.QRCode(
            version=2,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=2,
        )
        qr.add_data(self._build_reorder_url(item))
        qr.make(fit=True)
        image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        buffer.seek(0)
        return buffer

    def _build_reorder_url(self, item: InventoryItem) -> str:
        return f"{self.base_url.rstrip('/')}/scan/{item.id}"

    def _calculate_desired_stock(self, item: InventoryItem) -> int:
        return max(item.minimum_stock + item.reorder_quantity, item.reorder_quantity)

    @staticmethod
    def _chunk(sequence: Sequence[InventoryItem], size: int) -> Iterable[Sequence[InventoryItem]]:
        for index in range(0, len(sequence), size):
            yield sequence[index : index + size]

    def _normalize_filename(self, filename: str | None) -> str:
        if filename:
            clean = filename.replace(" ", "_").strip()
            if not clean.lower().endswith(".pdf"):
                clean += ".pdf"
            return clean
        timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
        return f"index_cards_{timestamp}.pdf"


def build_preview_payload(
    item: InventoryItem,
    renderer: IndexCardRenderer | None = None,
    blank_card: bool = False,
) -> dict:
    """Build a preview response payload.

    Args:
        item: Item to render
        renderer: Optional custom renderer
        blank_card: If True, render blank card with only QR code
    """
    renderer = renderer or IndexCardRenderer(blank_cards=blank_card)
    encoded_pdf = renderer.encode_preview(item, blank_card)
    card_type = "blank" if blank_card else "detailed"
    return {
        "item_id": str(item.id),
        "filename": f"{item.sku or item.id}_{card_type}_preview.pdf",
        "content_type": "application/pdf",
        "preview": encoded_pdf,
        "card_type": card_type,
    }
