"""Render a printable Avery 5371 business-card sheet of maker-box labels.

10 cards per US Letter page (2 columns × 5 rows), each 3.5" × 2".
Drawn at 300 DPI so a 1:1 print on plain paper or pre-cut Avery 5371
stock has the same proportions as the per-bin labels rendered by
:mod:`maker_boxes.services.label_service`. Each card reuses the
existing render_box_image layout (square QR encoding the assigned
username + member-name stack) so single-bin and multi-bin labels look
identical.

The warden hits this from the maker-box admin page when relabeling
a tray of bins or printing assignments in bulk after a member-renewal
sweep.
"""

from __future__ import annotations

from io import BytesIO
from typing import Sequence

from PIL import Image

from .label_service import render_box_image

# Avery 5371 / 5371X — 10 business cards per US Letter sheet.
DPI = 300
SHEET_PX = (int(8.5 * DPI), int(11 * DPI))  # 2550 × 3300
COLUMNS = 2
ROWS = 5
CARDS_PER_SHEET = COLUMNS * ROWS
CARD_PX = (int(3.5 * DPI), int(2 * DPI))  # 1050 × 600
TOP_MARGIN_PX = int(0.5 * DPI)  # 150
SIDE_MARGIN_PX = int(0.75 * DPI)  # 225


def render_avery_5371_sheet(boxes: Sequence) -> bytes:
    """Return PNG bytes for an Avery 5371 sheet (up to 10 maker boxes).

    Boxes beyond the 10th are dropped — the caller paginates if they
    have more. Unused slots stay blank so the warden can print on
    half-empty sheets without rendering glitches.

    Each card is rendered at the sheet's native 300 DPI so the
    per-card art matches the single-bin label exactly.
    """
    sheet = Image.new("RGB", SHEET_PX, "white")
    card_w, card_h = CARD_PX

    for idx, box in enumerate(list(boxes)[:CARDS_PER_SHEET]):
        col = idx % COLUMNS
        row = idx // COLUMNS
        x = SIDE_MARGIN_PX + col * card_w
        y = TOP_MARGIN_PX + row * card_h

        card = render_box_image(box, dpi=DPI)
        sheet.paste(card, (x, y))

    out = BytesIO()
    sheet.save(out, format="PNG", dpi=(DPI, DPI))
    out.seek(0)
    return out.getvalue()
