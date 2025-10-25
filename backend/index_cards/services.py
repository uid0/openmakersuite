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
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from django.utils import timezone
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
    """Render inventory items into 3x5" index cards for Avery 5388 sheets."""

    PAGE_WIDTH, PAGE_HEIGHT = letter
    CARD_WIDTH = 5 * inch
    CARD_HEIGHT = 3 * inch
    TOP_MARGIN = 1 * inch
    LEFT_MARGIN = (8.5 - 5) / 2 * inch  # Center card horizontally on the page
    VERTICAL_GAP = 0
    CARD_PADDING = 0.25 * inch
    IMAGE_MAX_WIDTH = 1.85 * inch
    IMAGE_MAX_HEIGHT = 1.85 * inch
    QR_CODE_SIZE = 1.7 * inch

    CALL_TO_ACTION = "Need to Re-Order? Scan this code to let Logistics know!"

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = base_url or getattr(settings, "FRONTEND_URL", "http://localhost:3000")
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
    def render_preview(self, item: InventoryItem) -> bytes:
        """Render a single-item preview and return PDF bytes."""

        buffer = BytesIO()
        self._render_to_canvas([item], buffer)
        return buffer.getvalue()

    def render_batch_to_storage(
        self, items: Sequence[InventoryItem], filename: str | None = None
    ) -> GeneratedCardFile:
        """Render cards for a sequence of items and persist the PDF."""

        if not items:
            raise ValueError("At least one item is required to render index cards.")

        normalized_name = self._normalize_filename(filename)
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

    def render_to_bytes(self, items: Sequence[InventoryItem]) -> bytes:
        """Render cards to PDF bytes without saving to storage."""

        buffer = BytesIO()
        self._render_to_canvas(items, buffer)
        return buffer.getvalue()

    def encode_preview(self, item: InventoryItem) -> str:
        """Return a base64 encoded preview PDF for quick display."""

        pdf_bytes = self.render_preview(item)
        return base64.b64encode(pdf_bytes).decode("ascii")

    # ------------------------------------------------------------------
    # Internal rendering helpers
    # ------------------------------------------------------------------
    def _render_to_canvas(self, items: Sequence[InventoryItem], buffer: BytesIO) -> None:
        pdf_canvas = canvas.Canvas(buffer, pagesize=letter)

        for page_items in self._chunk(items, 3):
            self._draw_page(pdf_canvas, page_items)
            pdf_canvas.showPage()

        pdf_canvas.save()
        buffer.seek(0)

    def _draw_page(self, pdf_canvas: canvas.Canvas, items: Sequence[InventoryItem]) -> None:
        for index, item in enumerate(items):
            y_offset = self.PAGE_HEIGHT - self.TOP_MARGIN - self.CARD_HEIGHT - index * (
                self.CARD_HEIGHT + self.VERTICAL_GAP
            )
            self._draw_card(pdf_canvas, item, self.LEFT_MARGIN, y_offset)

    def _draw_card(
        self, pdf_canvas: canvas.Canvas, item: InventoryItem, origin_x: float, origin_y: float
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

        # Title
        title_para = Paragraph(item.name, self._title_style)
        title_width, title_height = title_para.wrap(available_width, self.CARD_HEIGHT / 3)
        title_top = origin_y + self.CARD_HEIGHT - self.CARD_PADDING - title_height
        title_para.drawOn(pdf_canvas, inner_x, title_top)

        # Image placement block
        image_top = title_top - 12
        image_drawn_height = 0
        image_drawn_width = 0
        if item.image and hasattr(item.image, "path") and os.path.exists(item.image.path):
            image_reader = ImageReader(item.image.path)
            image_width, image_height = image_reader.getSize()
            scale = min(
                self.IMAGE_MAX_WIDTH / image_width,
                self.IMAGE_MAX_HEIGHT / image_height,
                1,
            )
            image_drawn_width = image_width * scale
            image_drawn_height = image_height * scale
            image_x = inner_x
            image_y = image_top - image_drawn_height
            pdf_canvas.drawImage(
                image_reader,
                image_x,
                image_y,
                width=image_drawn_width,
                height=image_drawn_height,
                preserveAspectRatio=True,
                mask="auto",
            )
        else:
            image_drawn_width = self.IMAGE_MAX_WIDTH
            image_drawn_height = 0
            pdf_canvas.setFont("Helvetica-Oblique", 9)
            placeholder_y = image_top - self.IMAGE_MAX_HEIGHT / 2
            pdf_canvas.drawCentredString(
                inner_x + self.IMAGE_MAX_WIDTH / 2,
                placeholder_y,
                "No image available",
            )

        text_x = inner_x + image_drawn_width + 12
        qr_x = origin_x + self.CARD_WIDTH - self.CARD_PADDING - self.QR_CODE_SIZE
        text_area_width = max(qr_x - 12 - text_x, 80)

        # Description
        description_para = Paragraph(item.description, self._body_style)
        _, description_height = description_para.wrap(text_area_width, self.CARD_HEIGHT / 2)
        description_top = title_top - 18
        description_para.drawOn(pdf_canvas, text_x, description_top - description_height)

        stats_y_start = description_top - description_height - 10
        desired_stock = self._calculate_desired_stock(item)
        reorder_threshold = item.minimum_stock
        info_lines = [
            f"Desired Stock Level: {desired_stock} units",
            f"Reorder Quantity: {item.reorder_quantity} units",
            f"Reorder When Stock ≤ {reorder_threshold} units",
            f"Current Stock: {item.current_stock} units",
        ]

        self._draw_info_lines(pdf_canvas, info_lines, text_x, stats_y_start, text_area_width)

        # QR code placement
        qr_buffer = self._generate_qr_code(item)
        qr_reader = ImageReader(qr_buffer)
        pdf_canvas.drawImage(
            qr_reader,
            qr_x,
            inner_y,
            width=self.QR_CODE_SIZE,
            height=self.QR_CODE_SIZE,
            preserveAspectRatio=True,
        )

        # Call to action centered near bottom, above QR code
        pdf_canvas.setFont("Helvetica-Bold", 12)
        call_to_action_y = inner_y + self.QR_CODE_SIZE + 8
        pdf_canvas.drawCentredString(
            origin_x + self.CARD_WIDTH / 2,
            call_to_action_y,
            self.CALL_TO_ACTION,
        )

    def _draw_info_lines(
        self,
        pdf_canvas: canvas.Canvas,
        lines: List[str],
        origin_x: float,
        origin_y: float,
        max_width: float,
    ) -> None:
        text_object = pdf_canvas.beginText()
        text_object.setTextOrigin(origin_x, origin_y)
        text_object.setFont(self._highlight_style.fontName, self._highlight_style.fontSize)
        leading = self._highlight_style.leading
        for line in lines:
            if not line:
                continue
            wrapped = self._wrap_text(line, max_width, self._highlight_style.fontName, self._highlight_style.fontSize)
            for fragment in wrapped:
                text_object.textLine(fragment)
        pdf_canvas.drawText(text_object)

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


def build_preview_payload(item: InventoryItem, renderer: IndexCardRenderer | None = None) -> dict:
    """Helper to build a preview response payload."""

    renderer = renderer or IndexCardRenderer()
    encoded_pdf = renderer.encode_preview(item)
    return {
        "item_id": str(item.id),
        "filename": f"{item.sku or item.id}_preview.pdf",
        "content_type": "application/pdf",
        "preview": encoded_pdf,
    }
