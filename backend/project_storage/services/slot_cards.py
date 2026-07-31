"""3×5 Avery cards for the project-storage racking.

Same stock as the inventory index cards — Avery 5388, three 5"×3" cards per
US-Letter sheet — so a warden prints a rack's worth of slot cards on the
sheets already in the supply cabinet. Each card carries three ways to
identify one physical slot:

* the eye-readable **code** (``1A1``) in the largest type that fits, because
  the fastest lookup on a rack aisle is still a human reading a label;
* a **QR** that opens the project-storage kiosk pre-filled with this slot, so
  a member reserving space scans the shelf instead of typing its code; and
* an **AprilTag** — the fiducial a "where is this item?" camera sweep reads.
  Slot markers come from the dedicated :data:`SLOT_TAG_FAMILY` pool
  (tag36h10), never the recycling tag36h11 pool that stints draw from, so a
  detected marker is unambiguous about *what kind* of thing it labels.

The renderer subclasses :class:`~index_cards.services.IndexCardRenderer` for
the sheet skeleton only: the 5388 constants (card size, margins, gap) and the
``_render_to_canvas``/``_draw_page``/``_chunk`` page loop, all of which are
pure geometry and know nothing about what a card holds. Everything below
``_draw_card`` in that class is ``InventoryItem``-coupled, so this one
overrides ``_draw_card`` outright rather than forcing a slot through the item
helpers.
"""

from __future__ import annotations

import base64
from io import BytesIO
from typing import Sequence
from urllib.parse import quote

import qrcode
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from fiducials.services.allocator import get_active_tag_id
from fiducials.services.apriltag_render import build_apriltag_image
from index_cards.services import IndexCardRenderer

from ..models import StorageSlot
from .storage_slots import SLOT_TAG_FAMILY

# Kiosk route the card's QR opens (frontend ``/project-storage/kiosk``). The
# ``slot`` query param is the canonical code — the same identifier the API
# looks slots up by — so the kiosk can resolve it without knowing our PKs.
KIOSK_PATH = "/project-storage/kiosk"
SLOT_QUERY_PARAM = "slot"

# Rendered marker resolution. The tag is drawn at ~1.15" on paper; 300 px
# keeps it well above the detector's px-per-module floor at every print DPI
# and matches the label service's "render big, let the PDF scale down" rule.
TAG_TARGET_PX = 300


class StorageSlotCardRenderer(IndexCardRenderer):
    """Render :class:`~project_storage.models.StorageSlot` cards on Avery 5388.

    Inherits the sheet layout from :class:`IndexCardRenderer` (see the module
    docstring) and replaces the card face. The parent's ``blank_cards`` flag
    is meaningless here — a slot card is always code + QR + marker.
    """

    # Both markers are square and share a right-hand column. 1.15" leaves a
    # readable QR and a detectable tag while keeping the code column wide.
    MARKER_SIZE = 1.15 * inch
    # Gap between the text column and the marker column.
    COLUMN_GAP = 0.15 * inch
    # Space reserved under each marker for its caption line.
    CAPTION_HEIGHT = 0.14 * inch
    CAPTION_FONT_SIZE = 6.5

    # Code type: start big, shrink until it fits its band. 84pt fits a
    # 3-character code across the column; long codes (``12Z40``) step down.
    CODE_FONT = "Helvetica-Bold"
    CODE_MAX_FONT_SIZE = 84
    CODE_MIN_FONT_SIZE = 22

    DETAIL_FONT = "Helvetica"
    DETAIL_FONT_SIZE = 9
    DETAIL_LEADING = 11

    _INK = colors.HexColor("#111827")
    _MUTED = colors.HexColor("#6B7280")

    # ------------------------------------------------------------------
    # Payload + marker sources
    # ------------------------------------------------------------------

    def build_slot_url(self, slot: StorageSlot) -> str:
        """The URL the card's QR encodes: the kiosk, pre-filled with ``slot``."""
        return (
            f"{self.base_url.rstrip('/')}{KIOSK_PATH}"
            f"?{SLOT_QUERY_PARAM}={quote(slot.code or '')}"
        )

    @staticmethod
    def tag_id_for(slot: StorageSlot) -> int | None:
        """The slot's permanent marker ID, or None when it has none.

        Prefers the ``active_april_tag_id`` annotation the viewset adds (one
        query for the whole sheet instead of one per card); falls back to a
        direct lookup for objects handed in from elsewhere. Same precedence
        as ``StorageSlotSerializer.get_april_tag_id`` so the printed card and
        the API payload can never disagree.
        """
        if hasattr(slot, "active_april_tag_id"):
            return slot.active_april_tag_id
        return get_active_tag_id(slot)

    def build_qr_buffer(self, slot: StorageSlot) -> BytesIO:
        """PNG bytes of the slot's QR, ready for :class:`ImageReader`."""
        qr = qrcode.QRCode(
            version=2,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=2,
        )
        qr.add_data(self.build_slot_url(slot))
        qr.make(fit=True)
        image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        buffer.seek(0)
        return buffer

    def build_tag_buffer(self, tag_id: int) -> BytesIO:
        """PNG bytes of the AprilTag for ``tag_id`` in the slot family."""
        buffer = BytesIO()
        build_apriltag_image(tag_id, TAG_TARGET_PX, family=SLOT_TAG_FAMILY).save(
            buffer, format="PNG"
        )
        buffer.seek(0)
        return buffer

    # ------------------------------------------------------------------
    # Card face
    # ------------------------------------------------------------------

    def _draw_card(  # type: ignore[override]
        self,
        pdf_canvas: canvas.Canvas,
        slot: StorageSlot,
        origin_x: float,
        origin_y: float,
    ) -> None:
        """Draw one slot card. ``origin`` is the card's bottom-left corner."""
        marker_x = origin_x + self.CARD_WIDTH - self.CARD_PADDING - self.MARKER_SIZE
        text_x = origin_x + self.CARD_PADDING
        text_width = marker_x - self.COLUMN_GAP - text_x

        self._draw_text_column(pdf_canvas, slot, text_x, origin_y, text_width)
        self._draw_qr(pdf_canvas, slot, marker_x, origin_y)
        self._draw_tag(pdf_canvas, slot, marker_x, origin_y)

    def _draw_text_column(
        self,
        pdf_canvas: canvas.Canvas,
        slot: StorageSlot,
        x: float,
        origin_y: float,
        width: float,
    ) -> None:
        top = origin_y + self.CARD_HEIGHT - self.CARD_PADDING

        pdf_canvas.setFillColor(self._MUTED)
        pdf_canvas.setFont("Helvetica-Bold", 9)
        pdf_canvas.drawString(x, top - 9, "PROJECT STORAGE")

        # Detail lines stack up from the card's bottom padding, so the code
        # band is whatever height is left over. Kept in the muted ink so the
        # code stays the thing you see from down the aisle.
        detail_y = origin_y + self.CARD_PADDING
        pdf_canvas.setFillColor(self._MUTED)
        pdf_canvas.setFont(self.DETAIL_FONT, self.DETAIL_FONT_SIZE)
        for line in reversed(self._detail_lines(slot)):
            pdf_canvas.drawString(x, detail_y, line)
            detail_y += self.DETAIL_LEADING

        band_bottom = detail_y + 2
        band_top = top - 16
        self._draw_code(pdf_canvas, slot, x, width, band_bottom, band_top)

    def _draw_code(
        self,
        pdf_canvas: canvas.Canvas,
        slot: StorageSlot,
        x: float,
        width: float,
        band_bottom: float,
        band_top: float,
    ) -> None:
        """Draw the location code as large as its band allows."""
        code = slot.code or StorageSlot.compose_code(slot.rack, slot.level, slot.position)
        band_height = max(band_top - band_bottom, 0)

        size = self.CODE_MAX_FONT_SIZE
        while size > self.CODE_MIN_FONT_SIZE:
            fits_width = pdf_canvas.stringWidth(code, self.CODE_FONT, size) <= width
            # Cap height is ~0.72 em for Helvetica; that (not the full em) is
            # what a digits-and-capitals string actually occupies.
            if fits_width and size * 0.72 <= band_height:
                break
            size -= 2

        cap_height = size * 0.72
        baseline = band_bottom + max(band_height - cap_height, 0) / 2
        pdf_canvas.setFillColor(self._INK)
        pdf_canvas.setFont(self.CODE_FONT, size)
        pdf_canvas.drawCentredString(x + width / 2, baseline, code)

    def _detail_lines(self, slot: StorageSlot) -> list[str]:
        """The small print under the code — everything a warden needs on the aisle."""
        lines = [f"Rack {slot.rack} - Level {slot.level} - Position {slot.position}"]
        if slot.requires_pallet_jack:
            lines.append("Pallet jack required")
        if slot.owning_group_id:
            lines.append(f"Reserved for {slot.owning_group.name}")
        if not slot.is_active:
            lines.append("Not in service")
        return lines

    def _draw_qr(
        self,
        pdf_canvas: canvas.Canvas,
        slot: StorageSlot,
        x: float,
        origin_y: float,
    ) -> None:
        y = origin_y + self.CARD_HEIGHT - self.CARD_PADDING - self.MARKER_SIZE
        pdf_canvas.drawImage(
            ImageReader(self.build_qr_buffer(slot)),
            x,
            y,
            width=self.MARKER_SIZE,
            height=self.MARKER_SIZE,
            preserveAspectRatio=True,
        )
        self._draw_caption(pdf_canvas, "Scan to reserve", x, y)

    def _draw_tag(
        self,
        pdf_canvas: canvas.Canvas,
        slot: StorageSlot,
        x: float,
        origin_y: float,
    ) -> None:
        """Draw the slot's location marker, or say so when it has none.

        A slot created while the tag family was exhausted is still perfectly
        printable — it just has no fiducial yet. Printing a placeholder note
        instead of some other slot's marker keeps the CV path honest; re-running
        the rack generator heals the slot and the reprint gets a real tag.
        """
        y = origin_y + self.CARD_PADDING + self.CAPTION_HEIGHT
        tag_id = self.tag_id_for(slot)
        if tag_id is None:
            self._draw_caption(pdf_canvas, "no location tag", x, y)
            return

        pdf_canvas.drawImage(
            ImageReader(self.build_tag_buffer(tag_id)),
            x,
            y,
            width=self.MARKER_SIZE,
            height=self.MARKER_SIZE,
            preserveAspectRatio=True,
        )
        self._draw_caption(pdf_canvas, f"{SLOT_TAG_FAMILY} #{tag_id}", x, y)

    def _draw_caption(
        self,
        pdf_canvas: canvas.Canvas,
        text: str,
        marker_x: float,
        marker_y: float,
    ) -> None:
        """Centre a caption line in the space reserved under a marker."""
        pdf_canvas.setFillColor(self._MUTED)
        pdf_canvas.setFont("Helvetica", self.CAPTION_FONT_SIZE)
        pdf_canvas.drawCentredString(
            marker_x + self.MARKER_SIZE / 2,
            marker_y - self.CAPTION_HEIGHT + 3,
            text,
        )


# ----------------------------------------------------------------------
# Module-level entry points — what the API layer calls
# ----------------------------------------------------------------------


def render_slot_cards(slots: Sequence[StorageSlot], *, base_url: str | None = None) -> bytes:
    """Render ``slots`` to a multi-page 5388 PDF (3 cards per page)."""
    if not slots:
        raise ValueError("At least one storage slot is required to render cards.")
    return StorageSlotCardRenderer(base_url=base_url).render_to_bytes(slots)


def build_slot_card_preview(
    slot: StorageSlot,
    renderer: StorageSlotCardRenderer | None = None,
) -> dict:
    """Single-slot preview payload — mirrors ``index_cards.build_preview_payload``.

    Also returns the kiosk URL and marker ID the card carries so a preview
    surface can show what was encoded without decoding the PDF.
    """
    renderer = renderer or StorageSlotCardRenderer()
    pdf_bytes = renderer.render_to_bytes([slot])
    return {
        "slot_id": slot.pk,
        "code": slot.code,
        "filename": f"storage_slot_{slot.code}_card.pdf",
        "content_type": "application/pdf",
        "preview": base64.b64encode(pdf_bytes).decode("ascii"),
        "kiosk_url": renderer.build_slot_url(slot),
        "april_tag_id": renderer.tag_id_for(slot),
    }
