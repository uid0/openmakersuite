"""Utilities for rendering inventory index cards."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Iterable, List, Sequence

import qrcode
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone
from inventory.models import InventoryItem
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph


@dataclass
class GeneratedCardFile:
    """Metadata about a generated PDF."""

    path: str
    url: str
    absolute_path: str | None


class IndexCardRenderer:
    """Render inventory items for Avery Template 5388 (3 cards per page, 5"×3" each)."""

    PAGE_WIDTH, PAGE_HEIGHT = letter
    CARD_WIDTH = 5 * inch  # Avery 5388: 5" wide
    CARD_HEIGHT = 3 * inch  # Avery 5388: 3" tall

    # Avery 5388 layout specifications
    TOP_MARGIN = 1.0 * inch  # Standard Avery margin
    BOTTOM_MARGIN = 1.0 * inch
    LEFT_MARGIN = 1.75 * inch  # Center cards: (8.5 - 5.0)/2 = 1.75"
    RIGHT_MARGIN = 1.75 * inch

    # Vertical spacing between cards (3 cards stacked vertically)
    CARD_GAP = 0.5 * inch  # Gap between cards

    # Card content optimized for 5"×3" landscape format
    CARD_PADDING = 0.15 * inch
    IMAGE_MAX_WIDTH = 2.0 * inch  # Fit in landscape layout
    IMAGE_MAX_HEIGHT = 2.0 * inch
    QR_CODE_SIZE = 0.8 * inch  # Smaller for compact 3" height

    CALL_TO_ACTION = "Scan to notify Logistics\nit's time to reorder me!"

    def __init__(self, base_url: str | None = None, blank_cards: bool = False) -> None:
        """Initialize the renderer with base URL and card type.

        Args:
            base_url: Base URL for QR codes (defaults to FRONTEND_URL setting)
            blank_cards: If True, render blank cards with only QR codes
        """
        self.base_url = base_url or getattr(settings, "FRONTEND_URL", "http://localhost:3000")
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
            normalized_name = normalized_name.replace(".pdf", "_blank.pdf")

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

    def render_to_bytes(self, items: Sequence[InventoryItem], blank_cards: bool = False) -> bytes:
        """Render cards to PDF bytes without saving to storage."""
        self.blank_cards = blank_cards
        buffer = BytesIO()
        self._render_to_canvas(items, buffer)
        return buffer.getvalue()

    def encode_preview(self, item: InventoryItem, blank_card: bool = False) -> str:
        """Return a base64 encoded preview PDF for quick display."""
        pdf_bytes = self.render_preview(item, blank_card)
        return base64.b64encode(pdf_bytes).decode("ascii")

    # ------------------------------------------------------------------
    # Internal rendering helpers
    # ------------------------------------------------------------------
    def _render_to_canvas(self, items: Sequence[InventoryItem], buffer: BytesIO) -> None:
        pdf_canvas = canvas.Canvas(buffer, pagesize=letter)

        for page_items in self._chunk(items, 3):  # 3 cards per page (Avery 5388)
            self._draw_page(pdf_canvas, page_items)
            pdf_canvas.showPage()

        pdf_canvas.save()
        buffer.seek(0)

    def _draw_page(self, pdf_canvas: canvas.Canvas, items: Sequence[InventoryItem]) -> None:
        # Draw up to 3 cards vertically (Avery 5388 layout)
        # Calculate available space for 3 cards + 2 gaps
        available_height = self.PAGE_HEIGHT - self.TOP_MARGIN - self.BOTTOM_MARGIN
        total_cards_height = 3 * self.CARD_HEIGHT
        total_gap_height = 2 * self.CARD_GAP

        # Adjust gaps if needed to fit perfectly
        if total_cards_height + total_gap_height > available_height:
            adjusted_gap = (available_height - total_cards_height) / 2
        else:
            adjusted_gap = self.CARD_GAP

        # Define the 3 vertical positions (stacked top to bottom)
        positions = []
        for i in range(3):
            x = self.LEFT_MARGIN
            y = (
                self.PAGE_HEIGHT
                - self.TOP_MARGIN
                - self.CARD_HEIGHT
                - i * (self.CARD_HEIGHT + adjusted_gap)
            )
            positions.append((x, y))

        for index, item in enumerate(items[:3]):  # Limit to 3 cards per page
            x_offset, y_offset = positions[index]
            self._draw_card(pdf_canvas, item, x_offset, y_offset)

        # No cutting marks needed - Avery 5388 is pre-perforated

    def _draw_card(
        self,
        pdf_canvas: canvas.Canvas,
        item: InventoryItem,
        origin_x: float,
        origin_y: float,
    ) -> None:
        """Draw a single inventory card with item details."""
        # Draw card border
        pdf_canvas.roundRect(
            origin_x,
            origin_y,
            self.CARD_WIDTH,
            self.CARD_HEIGHT,
            radius=12,
            stroke=1,
            fill=0,
        )

        if self.blank_cards:
            self._draw_blank_card(pdf_canvas, item, origin_x, origin_y)
        else:
            self._draw_detailed_card(pdf_canvas, item, origin_x, origin_y)

    def _draw_blank_card(
        self, pdf_canvas: canvas.Canvas, item: InventoryItem, origin_x: float, origin_y: float
    ) -> None:
        """Draw a blank card with only a centered QR code."""
        qr_x = origin_x + (self.CARD_WIDTH - self.QR_CODE_SIZE) / 2
        qr_y = origin_y + (self.CARD_HEIGHT - self.QR_CODE_SIZE) / 2

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

    def _draw_detailed_card(
        self, pdf_canvas: canvas.Canvas, item: InventoryItem, origin_x: float, origin_y: float
    ) -> None:
        """Draw a detailed card with title, info, image, QR code, and CTA."""
        inner_x = origin_x + self.CARD_PADDING
        inner_y = origin_y + self.CARD_PADDING
        available_width = self.CARD_WIDTH - 2 * self.CARD_PADDING

        # Define layout sections
        left_section_width = self.CARD_WIDTH * 0.6
        right_section_width = self.CARD_WIDTH * 0.4
        left_section_x = inner_x
        right_section_x = inner_x + left_section_width

        # Draw title and get updated Y position
        current_y = self._draw_title_section(pdf_canvas, item, inner_x, origin_y, available_width)

        # Draw left section (info + image)
        self._draw_left_section(
            pdf_canvas, item, left_section_x, left_section_width, current_y, inner_y
        )

        # Draw right section (QR + CTA)
        self._draw_right_section(
            pdf_canvas, item, right_section_x, right_section_width, current_y, inner_y
        )

        # Draw category at bottom
        self._draw_category_section(pdf_canvas, item, inner_x, inner_y)

    def _draw_title_section(
        self,
        pdf_canvas: canvas.Canvas,
        item: InventoryItem,
        inner_x: float,
        origin_y: float,
        available_width: float,
    ) -> float:
        """Draw the item title and return the updated Y position."""
        current_y = origin_y + self.CARD_HEIGHT - self.CARD_PADDING
        title_para = Paragraph(item.name, self._title_style)
        title_width, title_height = title_para.wrap(available_width, 0.4 * inch)
        title_para.drawOn(pdf_canvas, inner_x, current_y - title_height)
        return current_y - title_height - 0.3 * inch

    def _draw_left_section(
        self,
        pdf_canvas: canvas.Canvas,
        item: InventoryItem,
        left_section_x: float,
        left_section_width: float,
        current_y: float,
        inner_y: float,
    ) -> None:
        """Draw the left section with stock info and product image."""
        # Draw stock info
        target_stock = self._calculate_desired_stock(item)
        info_lines = [
            f"Target: {self._pluralize(target_stock, 'unit')}",
            f"Reorder: {self._pluralize(item.reorder_quantity, 'unit')}",
        ]

        if item.average_lead_time:
            info_lines.append(f"Lead: {self._pluralize(item.average_lead_time, 'day')}")

        info_y = current_y - 0.1 * inch
        self._draw_info_lines(
            pdf_canvas, info_lines, left_section_x, info_y, left_section_width - 0.1 * inch
        )

        # Draw product image below info
        info_lines_height = len(info_lines) * self._highlight_style.leading
        image_y_start = info_y - info_lines_height - 0.1 * inch

        if item.image and hasattr(item.image, "path") and os.path.exists(item.image.path):
            self._draw_product_image(
                pdf_canvas, item, left_section_x, left_section_width, image_y_start, inner_y
            )

    def _draw_right_section(
        self,
        pdf_canvas: canvas.Canvas,
        item: InventoryItem,
        right_section_x: float,
        right_section_width: float,
        current_y: float,
        inner_y: float,
    ) -> None:
        """Draw the right section with QR code and CTA."""
        # Calculate positioning for QR and CTA
        cta_dimensions = self._calculate_cta_dimensions(
            item, right_section_width, current_y, inner_y
        )
        qr_x, qr_y, cta_box = cta_dimensions

        # Draw QR code with optional frame
        self._draw_qr_code_with_frame(
            pdf_canvas, item, right_section_x, right_section_width, qr_x, qr_y
        )

        # Draw CTA box
        self._draw_cta_box(pdf_canvas, item, right_section_x, right_section_width, cta_box)

    def _draw_category_section(
        self, pdf_canvas: canvas.Canvas, item: InventoryItem, inner_x: float, inner_y: float
    ) -> None:
        """Draw the category text at the bottom of the card."""
        if not item.category:
            return

        category_color = item.category.color if item.category and item.category.color else "#2563eb"
        text_color = self._get_contrast_text_color(category_color)
        is_light_color = text_color.red == 0

        # Use category color for text if it's dark, otherwise use gray
        if not is_light_color and item.category.color and item.category.color.strip():
            try:
                category_text_color = colors.HexColor(item.category.color.strip())
                pdf_canvas.setFillColor(category_text_color)
            except (ValueError, AttributeError):
                pdf_canvas.setFillColor(colors.gray)
        else:
            pdf_canvas.setFillColor(colors.gray)

        pdf_canvas.setFont("Helvetica", 8)
        category_text = f"Category: {item.category.name}"
        pdf_canvas.drawString(inner_x, inner_y + 0.05 * inch, category_text)
        pdf_canvas.setFillColor(colors.black)  # Reset color

    def _draw_product_image(
        self,
        pdf_canvas: canvas.Canvas,
        item: InventoryItem,
        left_section_x: float,
        left_section_width: float,
        image_y_start: float,
        inner_y: float,
    ) -> None:
        """Draw the product image if available."""
        image_reader = ImageReader(item.image.path)
        image_width, image_height = image_reader.getSize()
        available_image_space = image_y_start - inner_y - 0.3 * inch  # Reserve space for category
        max_image_width = left_section_width - 0.2 * inch

        if available_image_space > 0:
            scale = min(max_image_width / image_width, available_image_space / image_height, 1)
            image_drawn_width = image_width * scale
            image_drawn_height = image_height * scale
            image_x = left_section_x + (left_section_width - image_drawn_width) / 2
            image_y = image_y_start - image_drawn_height
            pdf_canvas.drawImage(
                image_reader,
                image_x,
                image_y,
                width=image_drawn_width,
                height=image_drawn_height,
                preserveAspectRatio=True,
                mask="auto",
            )

    def _calculate_cta_dimensions(
        self, item: InventoryItem, right_section_width: float, current_y: float, inner_y: float
    ) -> tuple:
        """Calculate positioning for QR code and CTA box."""
        cta_lines = self.CALL_TO_ACTION.split("\n")
        line_height = 10
        padding_vertical = 3
        box_height = len(cta_lines) * line_height + 2 * padding_vertical
        box_width = right_section_width - 0.2 * inch

        category_space = 0.25 * inch if item.category else 0.1 * inch
        total_right_height = current_y - inner_y - category_space
        qr_and_cta_height = self.QR_CODE_SIZE + 0.1 * inch + box_height

        # Adjust positioning
        if qr_and_cta_height <= total_right_height:
            qr_y_adjusted = current_y - self.QR_CODE_SIZE
            box_y = qr_y_adjusted - 0.1 * inch - box_height
        else:
            qr_y_adjusted = current_y - self.QR_CODE_SIZE + 0.05 * inch
            box_y = qr_y_adjusted - 0.05 * inch - box_height

        # Ensure CTA doesn't go below category space
        min_box_y = inner_y + category_space
        if box_y < min_box_y:
            box_y = min_box_y
            if box_y + box_height > qr_y_adjusted - 0.02 * inch:
                line_height = 8
                box_height = len(cta_lines) * line_height + 2 * padding_vertical

        qr_x = 0  # Will be calculated in draw method
        return (
            qr_x,
            qr_y_adjusted,
            {
                "x": 0,
                "y": box_y,
                "width": box_width,
                "height": box_height,
                "line_height": line_height,
                "padding": padding_vertical,
            },
        )

    def _draw_qr_code_with_frame(
        self,
        pdf_canvas: canvas.Canvas,
        item: InventoryItem,
        right_section_x: float,
        right_section_width: float,
        qr_x: float,
        qr_y: float,
    ) -> None:
        """Draw QR code with optional colored frame."""
        qr_x = right_section_x + (right_section_width - self.QR_CODE_SIZE) / 2

        category_color = item.category.color if item.category and item.category.color else "#2563eb"
        text_color = self._get_contrast_text_color(category_color)
        is_light_color = text_color.red == 0

        # Add colored frame for light colors
        if is_light_color and item.category and item.category.color:
            try:
                frame_color = colors.HexColor(category_color.strip())
                frame_padding = 0.05 * inch
                pdf_canvas.setStrokeColor(frame_color)
                pdf_canvas.setLineWidth(2)
                pdf_canvas.rect(
                    qr_x - frame_padding,
                    qr_y - frame_padding,
                    self.QR_CODE_SIZE + 2 * frame_padding,
                    self.QR_CODE_SIZE + 2 * frame_padding,
                    stroke=1,
                    fill=0,
                )
            except (ValueError, AttributeError):
                pass

        # Draw QR code
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

    def _draw_cta_box(
        self,
        pdf_canvas: canvas.Canvas,
        item: InventoryItem,
        right_section_x: float,
        right_section_width: float,
        cta_box: dict,
    ) -> None:
        """Draw the call-to-action box with proper colors."""
        # Get background color
        if item.category and item.category.color and item.category.color.strip():
            try:
                bg_color = colors.HexColor(item.category.color.strip())
            except (ValueError, AttributeError):
                bg_color = colors.HexColor("#2563eb")
        else:
            bg_color = colors.HexColor("#2563eb")

        # Draw background box
        box_x = right_section_x + 0.05 * inch
        pdf_canvas.setFillColor(bg_color)
        pdf_canvas.roundRect(
            box_x, cta_box["y"], cta_box["width"], cta_box["height"], radius=3, stroke=0, fill=1
        )

        # Draw text with optimal contrast
        text_color = self._get_contrast_text_color(
            item.category.color if item.category and item.category.color else "#2563eb"
        )
        pdf_canvas.setFillColor(text_color)
        pdf_canvas.setFont("Helvetica-Bold", 8)

        cta_lines = self.CALL_TO_ACTION.split("\n")
        cta_y = cta_box["y"] + cta_box["height"] - cta_box["padding"] - 8
        for line in cta_lines:
            pdf_canvas.drawCentredString(right_section_x + right_section_width / 2, cta_y, line)
            cta_y -= cta_box["line_height"]

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

    def _draw_cutting_marks(self, pdf_canvas: canvas.Canvas, positions: list) -> None:
        """Avery 5388 is pre-perforated - no cutting marks needed."""
        # Avery Template 5388 comes with pre-perforated lines
        # Adding cutting marks would interfere with the template
        pass

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

    def _pluralize(self, count: int, word: str) -> str:
        """Return properly pluralized string based on count."""
        if count == 1:
            return f"{count} {word}"
        else:
            # Simple pluralization rules
            if word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
                # Consonant + y → ies (e.g., company → companies)
                plural = word[:-1] + "ies"
            elif word.endswith("y"):
                # Vowel + y → ys (e.g., day → days, key → keys)
                plural = word + "s"
            elif word.endswith(("s", "sh", "ch", "x", "z")):
                plural = word + "es"
            else:
                plural = word + "s"
            return f"{count} {plural}"

    def _get_contrast_text_color(self, hex_color: str) -> colors.Color:
        """
        Calculate optimal text color (black or white) for given background color.

        Uses luminance formula to determine if background is light or dark:
        Luminance = (0.299 * R + 0.587 * G + 0.114 * B) / 255
        """
        try:
            # Remove # if present and ensure we have 6 characters
            clean_hex = hex_color.strip().lstrip("#")
            if len(clean_hex) != 6:
                return colors.white  # Default to white for invalid colors

            # Convert hex to RGB
            r = int(clean_hex[0:2], 16)
            g = int(clean_hex[2:4], 16)
            b = int(clean_hex[4:6], 16)

            # Calculate relative luminance
            luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255

            # If luminance > 0.5, it's a light color - use black text
            # If luminance <= 0.5, it's a dark color - use white text
            if luminance > 0.5:
                return colors.black
            else:
                return colors.white

        except (ValueError, AttributeError):
            # Fallback to white for any parsing errors
            return colors.white

    @staticmethod
    def _chunk(
        sequence: Sequence[InventoryItem], size: int = 3
    ) -> Iterable[Sequence[InventoryItem]]:
        """Split items into chunks of specified size (default 3 for Avery 5388)."""
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
