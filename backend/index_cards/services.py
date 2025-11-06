"""
Utilities for rendering inventory index cards.

(C) 2025, Ian Wilson <me@ianwilson.org>
All Rights Reserved.
This file is licensed under the AGPL-3.0 license.
For more information, see the LICENSE file.

"""


from __future__ import annotations

import base64


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
    QR_CODE_SIZE = 1.2 * inch  # Increased size for better scanability

    CALL_TO_ACTION = "Scan to notify Logistics\nit's time to reorder me!"

    def __init__(self, base_url: str | None = None, blank_cards: bool = False) -> None:
        """Initialize the renderer with base URL and card type.

        Args:
            base_url: Base URL for QR codes (defaults to FRONTEND_URL setting)
            blank_cards: If True, render blank cards with only QR codes
        """
        self.base_url = base_url or getattr(
            settings, "FRONTEND_URL", "http://localhost:3000")
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
            raise ValueError(
                "At least one item is required to render index cards.")

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

        # 3 cards per page (Avery 5388)
        for page_items in self._chunk(items, 3):
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
        current_y = self._draw_title_section(
            pdf_canvas, item, inner_x, origin_y, available_width)

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

        # Draw Limited Quantity diamond for hazmat items
        if item.is_hazardous:
            self._draw_limited_quantity_diamond(
                pdf_canvas, item, inner_x, inner_y)

        # Draw shelf position arrow
        if item.shelf_position:
            self._draw_shelf_position_arrow(
                pdf_canvas, item, origin_x, origin_y)

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
        title_width, title_height = title_para.wrap(
            available_width, 0.4 * inch)
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
        # Draw Kanban stock info (reorder point and lead times)

        # Use custom reorder instruction if provided
        if item.reorder_instruction and item.reorder_instruction.strip():
            info_lines = [item.reorder_instruction.strip()]
        elif item.use_case_based_reorder:
            # Case-based reordering display
            info_lines = [
                f"Reorder at: {self._pluralize(item.minimum_cases, 'case')}",
            ]
        else:
            # Traditional unit-based display
            info_lines = [
                f"Reorder at: {self._pluralize(item.minimum_stock, 'unit')}",
            ]

        # Add average lead time from primary supplier
        if item.average_lead_time:
            info_lines.append(
                f"Avg Lead: {self._pluralize(item.average_lead_time, 'day')}")

        # Add longest lead time across all suppliers
        longest_lead_time = self._get_longest_lead_time(item)
        if longest_lead_time and longest_lead_time != item.average_lead_time:
            info_lines.append(
                f"Max Lead: {self._pluralize(longest_lead_time, 'day')}")

        info_y = current_y - 0.1 * inch
        self._draw_info_lines(
            pdf_canvas, info_lines, left_section_x, info_y, left_section_width - 0.1 * inch
        )

        # Draw hazmat indicator if item is hazardous
        info_lines_height = len(info_lines) * self._highlight_style.leading
        hazmat_y = info_y - info_lines_height - 0.15 * inch

        hazmat_height = 0
        if item.is_hazardous:
            hazmat_height = self._draw_hazmat_indicator(
                pdf_canvas, item, left_section_x, left_section_width, hazmat_y
            )

        # Draw product image below info and hazmat indicator
        image_y_start = hazmat_y - hazmat_height - 0.1 * inch

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
        self._draw_cta_box(pdf_canvas, item, right_section_x,
                           right_section_width, cta_box)

    def _draw_hazmat_indicator(
        self,
        pdf_canvas: canvas.Canvas,
        item: InventoryItem,
        left_section_x: float,
        left_section_width: float,
        y_position: float,
    ) -> float:
        """Draw hazmat indicator - either NFPA diamond or HAZMAT text.

        Returns:
            Height of the drawn indicator
        """
        # Check if we have complete NFPA data
        has_nfpa_data = (
            item.nfpa_health_hazard is not None
            and item.nfpa_fire_hazard is not None
            and item.nfpa_instability_hazard is not None
        )

        if has_nfpa_data:
            # Draw NFPA diamond
            return self._draw_nfpa_diamond(
                pdf_canvas, item, left_section_x, left_section_width, y_position
            )
        else:
            # Draw HAZMAT text
            return self._draw_hazmat_text(pdf_canvas, left_section_x, y_position)

    def _draw_nfpa_diamond(
        self,
        pdf_canvas: canvas.Canvas,
        item: InventoryItem,
        left_section_x: float,
        left_section_width: float,
        y_position: float,
    ) -> float:
        """Draw NFPA 704 fire diamond.

        Returns:
            Height of the diamond
        """
        diamond_size = 0.8 * inch
        half_size = diamond_size / 2

        # Center the diamond horizontally
        center_x = left_section_x + left_section_width / 2
        center_y = y_position - half_size

        # Define diamond quadrants (rotated 45 degrees)
        # Top (Health - Blue), Right (Fire - Red), Bottom (Reactivity - Yellow), Left (Special - White)

        # Draw the four diamond sections
        sections = [
            {  # Top - Health (Blue)
                "points": [
                    (center_x, center_y + half_size),
                    (center_x - half_size, center_y),
                    (center_x, center_y),
                    (center_x + half_size, center_y),
                ],
                "color": colors.HexColor("#0000FF"),
                "value": item.nfpa_health_hazard,
            },
            {  # Right - Fire (Red)
                "points": [
                    (center_x + half_size, center_y),
                    (center_x, center_y + half_size),
                    (center_x, center_y),
                    (center_x, center_y - half_size),
                ],
                "color": colors.HexColor("#FF0000"),
                "value": item.nfpa_fire_hazard,
            },
            {  # Bottom - Instability/Reactivity (Yellow)
                "points": [
                    (center_x, center_y - half_size),
                    (center_x + half_size, center_y),
                    (center_x, center_y),
                    (center_x - half_size, center_y),
                ],
                "color": colors.HexColor("#FFFF00"),
                "value": item.nfpa_instability_hazard,
            },
            {  # Left - Special Hazards (White)
                "points": [
                    (center_x - half_size, center_y),
                    (center_x, center_y - half_size),
                    (center_x, center_y),
                    (center_x, center_y + half_size),
                ],
                "color": colors.white,
                "value": item.nfpa_special_hazards or "",
            },
        ]

        # Draw each section
        for section in sections:
            path = pdf_canvas.beginPath()
            path.moveTo(section["points"][0][0], section["points"][0][1])
            for point in section["points"][1:]:
                path.lineTo(point[0], point[1])
            path.close()

            pdf_canvas.setFillColor(section["color"])
            pdf_canvas.setStrokeColor(colors.black)
            pdf_canvas.setLineWidth(1)
            pdf_canvas.drawPath(path, stroke=1, fill=1)

        # Draw values/text in each section
        pdf_canvas.setFillColor(colors.black)
        pdf_canvas.setFont("Helvetica-Bold", 14)

        # Health (top)
        pdf_canvas.drawCentredString(
            center_x, center_y + half_size / 2 - 5, str(sections[0]["value"])
        )

        # Fire (right)
        pdf_canvas.drawCentredString(
            center_x + half_size / 2, center_y - 5, str(sections[1]["value"])
        )

        # Instability (bottom)
        pdf_canvas.drawCentredString(
            center_x, center_y - half_size / 2 - 5, str(sections[2]["value"])
        )

        # Special (left) - may be text
        special_value = str(sections[3]["value"]) if sections[3]["value"] else ""
        if special_value:
            pdf_canvas.setFont("Helvetica-Bold", 10)
            pdf_canvas.drawCentredString(center_x - half_size / 2, center_y - 5, special_value)

        return diamond_size

    def _draw_hazmat_text(
        self, pdf_canvas: canvas.Canvas, left_section_x: float, y_position: float
    ) -> float:
        """Draw HAZMAT text indicator.

        Returns:
            Height of the text
        """
        pdf_canvas.setFillColor(colors.HexColor("#FF0000"))
        pdf_canvas.setFont("Helvetica-Bold", 16)
        pdf_canvas.drawString(left_section_x, y_position - 0.2 * inch, "⚠ HAZMAT")
        pdf_canvas.setFillColor(colors.black)  # Reset color
        return 0.25 * inch

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
                category_text_color = colors.HexColor(
                    item.category.color.strip())
                pdf_canvas.setFillColor(category_text_color)
            except (ValueError, AttributeError):
                pdf_canvas.setFillColor(colors.gray)
        else:
            pdf_canvas.setFillColor(colors.gray)

        pdf_canvas.setFont("Helvetica", 8)
        category_text = f"Category: {item.category.name}"
        pdf_canvas.drawString(inner_x, inner_y + 0.05 * inch, category_text)
        pdf_canvas.setFillColor(colors.black)  # Reset color

    def _draw_limited_quantity_diamond(
        self, pdf_canvas: canvas.Canvas, item: InventoryItem, inner_x: float, inner_y: float
    ) -> None:
        """Draw the Limited Quantity diamond for hazmat items."""
        # Limited Quantity diamond size
        diamond_size = 0.4 * inch
        # Position in top-right corner
        diamond_x = inner_x + self.CARD_WIDTH - 2 * self.CARD_PADDING - diamond_size
        diamond_y = inner_y + self.CARD_HEIGHT - 2 * self.CARD_PADDING - diamond_size

        # Draw diamond shape (rotated square)
        pdf_canvas.saveState()
        pdf_canvas.translate(diamond_x + diamond_size / 2,
                             diamond_y + diamond_size / 2)
        pdf_canvas.rotate(45)

        # Draw white background
        pdf_canvas.setFillColor(colors.white)
        pdf_canvas.setStrokeColor(colors.black)
        pdf_canvas.setLineWidth(2)
        pdf_canvas.rect(
            -diamond_size / 2, -diamond_size / 2, diamond_size, diamond_size, stroke=1, fill=1
        )

        # Draw "Y" symbol (Limited Quantity marking)
        pdf_canvas.setFillColor(colors.black)
        pdf_canvas.setFont("Helvetica-Bold", 20)
        pdf_canvas.drawCentredString(0, -7, "Y")

        pdf_canvas.restoreState()

    def _draw_shelf_position_arrow(
        self,
        pdf_canvas: canvas.Canvas,
        item: InventoryItem,
        origin_x: float,
        origin_y: float,
    ) -> None:
        """Draw up or down arrow for shelf position."""
        arrow_size = 0.3 * inch
        # Position in top-left corner
        arrow_x = origin_x + self.CARD_PADDING
        arrow_y = origin_y + self.CARD_HEIGHT - self.CARD_PADDING - arrow_size

        pdf_canvas.setFillColor(colors.black)
        pdf_canvas.setStrokeColor(colors.black)
        pdf_canvas.setLineWidth(2)

        if item.shelf_position == "top":
            # Draw up arrow (▲)
            points = [
                (arrow_x + arrow_size / 2, arrow_y + arrow_size),  # Top point
                (arrow_x, arrow_y),  # Bottom left
                (arrow_x + arrow_size, arrow_y),  # Bottom right
            ]
            pdf_canvas.polygon(points, stroke=1, fill=1)
        elif item.shelf_position == "bottom":
            # Draw down arrow (▼)
            points = [
                (arrow_x + arrow_size / 2, arrow_y),  # Top point
                (arrow_x, arrow_y + arrow_size),  # Bottom left
                (arrow_x + arrow_size, arrow_y + arrow_size),  # Bottom right
            ]
            pdf_canvas.polygon(points, stroke=1, fill=1)

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
        available_image_space = image_y_start - inner_y - \
            0.3 * inch  # Reserve space for category
        max_image_width = left_section_width - 0.2 * inch

        if available_image_space > 0:
            scale = min(max_image_width / image_width,
                        available_image_space / image_height, 1)
            image_drawn_width = image_width * scale
            image_drawn_height = image_height * scale
            image_x = left_section_x + \
                (left_section_width - image_drawn_width) / 2
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
                box_height = len(cta_lines) * line_height + \
                    2 * padding_vertical

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
            pdf_canvas.drawCentredString(
                right_section_x + right_section_width / 2, cta_y, line)
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
        text_object.setFont(self._highlight_style.fontName,
                            self._highlight_style.fontSize)
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
        image = qr.make_image(fill_color="black",
                              back_color="white").convert("RGB")
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        buffer.seek(0)
        return buffer

    def _build_reorder_url(self, item: InventoryItem) -> str:
        return f"{self.base_url.rstrip('/')}/scan/{item.id}"

    def _calculate_desired_stock(self, item: InventoryItem) -> int:
        return max(item.minimum_stock + item.reorder_quantity, item.reorder_quantity)

    def _get_longest_lead_time(self, item: InventoryItem) -> int | None:
        """Get the longest lead time across all suppliers for this item."""
        if not hasattr(item, "item_suppliers"):
            return None

        lead_times = []
        for supplier_link in item.item_suppliers.all():
            if supplier_link.average_lead_time:
                lead_times.append(supplier_link.average_lead_time)

        return max(lead_times) if lead_times else None

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
            yield sequence[index:index + size]

    def _normalize_filename(self, filename: str | None) -> str:
        if filename:
            clean = filename.replace(" ", "_").strip()
            if not clean.lower().endswith(".pdf"):
                clean += ".pdf"
            return clean
        timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
        return f"index_cards_{timestamp}.pdf"


class FixtureCardRenderer(IndexCardRenderer):
    """Render fixture refill request cards leveraging the index card layout."""

    CALL_TO_ACTION = "Scan this code to request a refill"

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__(base_url=base_url, blank_cards=False)
        self._subtitle_style = ParagraphStyle(
            name="FixtureSubtitle",
            fontName="Helvetica",
            fontSize=12,
            leading=14,
            alignment=TA_LEFT,
            textColor=colors.HexColor("#1F2937"),
        )
        self._summary_style = ParagraphStyle(
            name="FixtureSummary",
            fontName="Helvetica",
            fontSize=11,
            leading=13,
            alignment=TA_LEFT,
            textColor=colors.HexColor("#111827"),
        )
        self._cta_style = ParagraphStyle(
            name="FixtureCTA",
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=14,
            alignment=TA_LEFT,
            textColor=colors.white,
        )

    # Public helpers -------------------------------------------------
    def render_preview(self, fixture: Fixture, blank_card: bool = False) -> bytes:
        if blank_card:
            raise ValueError("Fixture cards do not support blank mode.")
        return super().render_preview(fixture, blank_card=False)

    def render_to_bytes(self, fixtures: Sequence[Fixture], blank_cards: bool = False) -> bytes:
        if blank_cards:
            raise ValueError("Fixture cards do not support blank mode.")
        return super().render_to_bytes(fixtures, blank_cards=False)

    def render_batch_to_storage(
        self,
        fixtures: Sequence[Fixture],
        filename: str | None = None,
        blank_cards: bool = False,
    ) -> GeneratedCardFile:
        if blank_cards:
            raise ValueError("Fixture cards do not support blank mode.")
        return super().render_batch_to_storage(fixtures, filename=filename, blank_cards=False)

    # Overrides ------------------------------------------------------
    def _draw_card(
        self,
        pdf_canvas: canvas.Canvas,
        fixture: Fixture,
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

        # Reserve space for QR code on the right
        qr_offset = 0.2 * inch
        text_width = available_width - self.QR_CODE_SIZE - qr_offset
        qr_x = inner_x + text_width + qr_offset / 2
        qr_y = inner_y + 0.65 * inch

        # Title
        top_y = origin_y + self.CARD_HEIGHT - self.CARD_PADDING
        title_para = Paragraph(fixture.name, self._title_style)
        title_width, title_height = title_para.wrap(text_width, 0.7 * inch)
        title_para.drawOn(pdf_canvas, inner_x, top_y - title_height)
        current_y = top_y - title_height - 0.1 * inch

        # Location
        location_name = fixture.location.name if fixture.location else "Unknown location"
        location_para = Paragraph(f"<b>Location:</b> {location_name}", self._subtitle_style)
        _, location_height = location_para.wrap(text_width, 0.5 * inch)
        location_para.drawOn(pdf_canvas, inner_x, current_y - location_height)
        current_y -= location_height + 0.08 * inch

        # Refill item and identifiers
        summary_lines: list[str] = []
        if fixture.refill_item:
            summary_lines.append(f"<b>Refill Item:</b> {fixture.refill_item.name}")
            if fixture.refill_item.sku:
                summary_lines.append(f"<b>SKU:</b> {fixture.refill_item.sku}")
        if fixture.asset_tag:
            summary_lines.append(f"<b>Fixture ID:</b> {fixture.asset_tag}")

        for line in summary_lines:
            summary_para = Paragraph(line, self._summary_style)
            _, summary_height = summary_para.wrap(text_width, 0.4 * inch)
            summary_para.drawOn(pdf_canvas, inner_x, current_y - summary_height)
            current_y -= summary_height + 0.05 * inch

        # CTA box near bottom-left
        cta_box_width = text_width
        cta_box_height = 0.9 * inch
        cta_box_x = inner_x
        cta_box_y = inner_y + 0.2 * inch

        pdf_canvas.saveState()
        pdf_canvas.setFillColor(colors.HexColor("#1D4ED8"))
        pdf_canvas.roundRect(
            cta_box_x,
            cta_box_y,
            cta_box_width,
            cta_box_height,
            radius=10,
            stroke=0,
            fill=1,
        )
        pdf_canvas.restoreState()

        cta_para = Paragraph(
            f"{self.CALL_TO_ACTION}<br/>Let Logistics know when this fixture needs attention.",
            self._cta_style,
        )
        cta_para.wrapOn(pdf_canvas, cta_box_width - 0.2 * inch, cta_box_height - 0.2 * inch)
        cta_para.drawOn(pdf_canvas, cta_box_x + 0.1 * inch, cta_box_y + 0.25 * inch)

        # QR code
        qr_buffer = self._generate_qr_code(fixture)
        qr_reader = ImageReader(qr_buffer)
        pdf_canvas.drawImage(
            qr_reader,
            qr_x,
            qr_y,
            width=self.QR_CODE_SIZE,
            height=self.QR_CODE_SIZE,
            preserveAspectRatio=True,
        )

        # QR caption
        pdf_canvas.setFont("Helvetica", 9)
        caption = "Point your phone camera here to submit a refill request."
        text_width_measure = pdfmetrics.stringWidth(caption, "Helvetica", 9)
        caption_x = qr_x + (self.QR_CODE_SIZE - text_width_measure) / 2
        caption_y = qr_y - 0.25 * inch
        pdf_canvas.drawString(caption_x, caption_y, caption)

    def _build_reorder_url(self, fixture: Fixture) -> str:
        return f"{self.base_url.rstrip('/')}/scan/fixture/{fixture.id}"

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
