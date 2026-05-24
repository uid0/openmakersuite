"""Render a Project Storage label as PNG.

Designed for two physical printer families:
  - Brother QL-series label printer (recommended): pulls a 696×271-px
    portrait PNG sized for a 62mm × ~24mm continuous label at 300 DPI.
  - Epson TM-T-series receipt printer: 576-px-wide PNG sized for 80mm
    receipt paper at 203 DPI.

The same renderer drives both — caller picks the layout via the
``printer`` kwarg. Layout:

    ┌───────────────────────────┐
    │  [QR ~140px]   Wk 47      │   ← QR encodes stint_id; right
    │                Day 327    │     side: expiry week / DoY in big bold
    │                           │
    │  PS-AB23CDFG              │   ← eye-readable stint id
    │  Member Name              │
    │  Project: <title>         │
    └───────────────────────────┘
"""

from __future__ import annotations

from io import BytesIO
from typing import Iterable, Literal

import qrcode
from PIL import Image, ImageDraw, ImageFont

PrinterFamily = Literal["brother_ql", "epson_tm"]

# Brother QL: 62mm continuous tape × ~24mm visible — 696×271 at 300 DPI
# (matching brother_ql library defaults for 62mm media).
BROTHER_SIZE_PX = (696, 271)

# Epson TM-T: 80mm paper = 576 px-wide print area at 203 DPI.
# Height chosen to keep the layout square-ish at this width.
EPSON_SIZE_PX = (576, 280)


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
    # Round-trip through PNG bytes — qrcode returns a wrapper that some
    # consumers can't isinstance-check. Same pattern as
    # inventory.services.qr_code_service after the Django 6 / Pillow 12
    # rework.
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    real = Image.open(buf).convert("RGB")
    return real.resize((target_px, target_px), Image.Resampling.NEAREST)


def _draw_right_column_bigtext(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    x: int,
    y_top: int,
    available_w: int,
    available_h: int,
    week: int,
    day_of_year: int,
) -> None:
    """Draw the expiry week / day-of-year stack in the largest bold font
    that fits the right-hand column."""

    big_size = max(40, available_h // 3)
    line1 = f"Wk {week:02d}"
    line2 = f"Day {day_of_year:03d}"
    font = _try_font(big_size)
    while big_size > 18:
        font = _try_font(big_size)
        w1, h1 = draw.textbbox((0, 0), line1, font=font)[2:]
        w2, h2 = draw.textbbox((0, 0), line2, font=font)[2:]
        if max(w1, w2) <= available_w and (h1 + h2 + 6) <= available_h:
            break
        big_size -= 4

    h1 = draw.textbbox((0, 0), line1, font=font)[3]
    draw.text((x, y_top), line1, fill="black", font=font)
    draw.text((x, y_top + h1 + 6), line2, fill="black", font=font)


def render_stint_label(
    stint,
    *,
    printer: PrinterFamily = "brother_ql",
) -> bytes:
    """Return PNG bytes for the stint's label, ready to feed to the printer."""

    size = BROTHER_SIZE_PX if printer == "brother_ql" else EPSON_SIZE_PX
    width, height = size
    canvas = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(canvas)

    margin = 12
    qr_px = height - 2 * margin
    qr = _build_qr_image(stint.stint_id, qr_px)
    canvas.paste(qr, (margin, margin))

    text_x = margin + qr_px + 16
    available_w = width - text_x - margin

    # Right column: big bold expiry — split the column so the top 60% is
    # the date stack, bottom 40% is the eye-readable id + member.
    top_h = int(height * 0.55)
    week, doy = stint.expiry_week_and_day
    _draw_right_column_bigtext(
        canvas,
        draw,
        x=text_x,
        y_top=margin,
        available_w=available_w,
        available_h=top_h,
        week=week,
        day_of_year=doy,
    )

    # Bottom strip: stint_id, member name, project title.
    bottom_y = margin + top_h + 8
    bottom_h = height - bottom_y - margin
    small_font = _try_font(max(14, bottom_h // 4))
    lines = [
        stint.stint_id,
        stint.display_name,
    ]
    if stint.project_title:
        lines.append(f"Proj: {stint.project_title}")
    y = bottom_y
    for line in lines:
        draw.text((text_x, y), line, fill="black", font=small_font)
        bbox = draw.textbbox((0, 0), line, font=small_font)
        y += (bbox[3] - bbox[1]) + 4

    out = BytesIO()
    canvas.save(out, format="PNG")
    out.seek(0)
    return out.getvalue()
