"""Render a printable Avery 5371 business-card sheet of stint labels.

10 cards per US Letter page (2 columns × 5 rows), each 3.5" × 2".
Drawn at 300 DPI so a 1:1 print on plain paper or pre-cut Avery 5371
stock has the same proportions as the Brother QL labels — same QR
payload (URL deep link), same eye-readable stint id, same big
expiry-week stack the warden uses to triage a shelf at a glance.

Layout per card:

    ┌─────────────────────────────────┐
    │  [QR ~552px]   Wk 47            │
    │                Day 327          │
    │                                 │
    │                PS-AB23CDFG      │
    │                Member Name      │
    │                Proj: <title>    │
    └─────────────────────────────────┘
"""

from __future__ import annotations

from io import BytesIO
from typing import Iterable, Sequence

from django.conf import settings

import qrcode
from PIL import Image, ImageDraw, ImageFont

# Avery 5371 / 5371X — 10 business cards per US Letter sheet.
DPI = 300
SHEET_PX = (int(8.5 * DPI), int(11 * DPI))  # 2550 × 3300
COLUMNS = 2
ROWS = 5
CARDS_PER_SHEET = COLUMNS * ROWS
CARD_PX = (int(3.5 * DPI), int(2 * DPI))  # 1050 × 600
TOP_MARGIN_PX = int(0.5 * DPI)  # 150
SIDE_MARGIN_PX = int(0.75 * DPI)  # 225


def _try_font(size: int) -> ImageFont.ImageFont:
    candidates: Iterable[str] = (
        "DejaVuSans-Bold.ttf",
        "DejaVuSans.ttf",
        "Arial Bold.ttf",
        "Arial.ttf",
    )
    for name in candidates:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _build_qr_image(value: str, target_px: int) -> Image.Image:
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=1,
    )
    qr.add_data(value)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    # Round-trip through PNG bytes — qrcode returns a wrapper that
    # some consumers can't isinstance-check. Same pattern as
    # project_storage/services/label_service.py.
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    real = Image.open(buf).convert("RGB")
    return real.resize((target_px, target_px), Image.Resampling.NEAREST)


def _shrink_to_fit(
    draw: ImageDraw.ImageDraw,
    lines: Sequence[str],
    available_w: int,
    start_size: int,
    min_size: int,
) -> ImageFont.ImageFont:
    size = start_size
    while size > min_size:
        font = _try_font(size)
        widest = max(draw.textbbox((0, 0), line, font=font)[2] for line in lines)
        if widest <= available_w:
            return font
        size -= 4
    return _try_font(min_size)


def _render_card(card: Image.Image, stint) -> None:
    """Render one stint into a pre-allocated card canvas."""
    draw = ImageDraw.Draw(card)
    width, height = card.size
    margin = 24

    qr_px = height - 2 * margin
    frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000").rstrip("/")
    qr_payload = f"{frontend_url}/scan/project-storage/{stint.stint_id}"
    qr = _build_qr_image(qr_payload, qr_px)
    card.paste(qr, (margin, margin))

    text_x = margin + qr_px + 24
    available_w = width - text_x - margin

    week, doy = stint.expiry_week_and_day
    big_lines = [f"Wk {week:02d}", f"Day {doy:03d}"]
    big = _shrink_to_fit(draw, big_lines, available_w, start_size=84, min_size=36)
    h1 = draw.textbbox((0, 0), big_lines[0], font=big)[3]
    draw.text((text_x, margin), big_lines[0], fill="black", font=big)
    draw.text((text_x, margin + h1 + 6), big_lines[1], fill="black", font=big)

    bottom_lines = [stint.stint_id, stint.display_name]
    if stint.project_title:
        bottom_lines.append(f"Proj: {stint.project_title[:32]}")
    small = _shrink_to_fit(draw, bottom_lines, available_w, start_size=34, min_size=18)
    bottom_y = margin + (h1 + 6) * 2 + 24
    y = bottom_y
    for line in bottom_lines:
        draw.text((text_x, y), line, fill="black", font=small)
        bbox = draw.textbbox((0, 0), line, font=small)
        y += (bbox[3] - bbox[1]) + 6


def render_business_card_sheet(stints: Sequence) -> bytes:
    """Return PNG bytes for an Avery 5371 sheet (up to 10 stints).

    Stints beyond the 10th are dropped — the caller paginates if they
    have more. Unused slots are left blank so the warden can print on
    half-empty sheets without rendering glitches.
    """
    sheet = Image.new("RGB", SHEET_PX, "white")
    card_w, card_h = CARD_PX

    for idx, stint in enumerate(list(stints)[:CARDS_PER_SHEET]):
        col = idx % COLUMNS
        row = idx // COLUMNS
        x = SIDE_MARGIN_PX + col * card_w
        y = TOP_MARGIN_PX + row * card_h

        card = Image.new("RGB", CARD_PX, "white")
        _render_card(card, stint)
        sheet.paste(card, (x, y))

    out = BytesIO()
    sheet.save(out, format="PNG", dpi=(DPI, DPI))
    out.seek(0)
    return out.getvalue()
