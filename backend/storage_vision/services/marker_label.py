"""Render a printable marker label for one VisionSlot (AC-6).

Output is a PNG sized for a typical Avery 5371 business-card slot
(3.5" × 2" at 300 DPI = 1050 × 600 px). The card contains:

  - The human-readable marker code (big mono)
  - The item name and area name underneath
  - A QR encoding ONLY the marker code payload — the spec is explicit
    that a slot label must not encode any other field. A camera that
    sees this QR resolves to the slot via VisionSlot.marker_code, no
    JWT or URL leakage involved.

Layout mirrors maker_boxes' render_box_image so a future Avery 5371
multi-up sheet (the same one we ship for maker boxes) can paste a
storage-vision card into a mixed sheet without re-engineering.
"""

from __future__ import annotations

from io import BytesIO
from typing import Iterable

import qrcode
from PIL import Image, ImageDraw, ImageFont

LABEL_WIDTH_INCHES = 3.5
LABEL_HEIGHT_INCHES = 2.0
DEFAULT_DPI = 300
INNER_MARGIN_INCHES = 0.1
QR_SIZE_INCHES = 1.6
TEXT_GAP_INCHES = 0.15


def _try_font(size: int) -> ImageFont.ImageFont:
    candidates: Iterable[str] = (
        "DejaVuSansMono-Bold.ttf",
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


def _measure(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _fit_font(
    draw: ImageDraw.ImageDraw, text: str, max_w: int, max_h: int, start: int
) -> ImageFont.ImageFont:
    size = max(12, start)
    while size > 12:
        font = _try_font(size)
        w, h = _measure(draw, text, font)
        if w <= max_w and h <= max_h:
            return font
        size -= 4
    return _try_font(12)


def _build_qr(payload: str, target_px: int) -> Image.Image:
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=1,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    # Round-trip through PNG so consumers that isinstance-check on
    # PIL.Image.Image get the right shape (same pattern as
    # project_storage/services/label_service.py).
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return Image.open(buf).convert("RGB").resize((target_px, target_px), Image.Resampling.NEAREST)


def render_slot_marker_label(slot, *, dpi: int = DEFAULT_DPI) -> bytes:
    """Return PNG bytes for the slot's printable marker label.

    ``slot`` may be a :class:`VisionSlot` instance or any object exposing
    ``marker_code``, ``item.name``, and ``area.name`` — keeping the seam
    so tests can drive this with a minimal fake.
    """
    width = int(round(LABEL_WIDTH_INCHES * dpi))
    height = int(round(LABEL_HEIGHT_INCHES * dpi))
    margin = int(round(INNER_MARGIN_INCHES * dpi))
    qr_px = int(round(QR_SIZE_INCHES * dpi))
    gap = int(round(TEXT_GAP_INCHES * dpi))

    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    # QR sits on the left, vertically centered.
    qr_x = margin
    qr_y = (height - qr_px) // 2
    qr = _build_qr(slot.marker_code, qr_px)
    img.paste(qr, (qr_x, qr_y))

    text_x = qr_x + qr_px + gap
    text_right = width - margin
    text_w = max(text_right - text_x, 1)

    # Marker code (big, monospace-ish) on top.
    code_h = int(height * 0.45)
    code_font = _fit_font(draw, slot.marker_code, text_w, code_h, start=int(dpi * 0.55))
    code_y = margin
    draw.text((text_x, code_y), slot.marker_code, fill="black", font=code_font)

    # Item + area underneath in smaller copy. Truncate long names so
    # the layout stays predictable.
    line2 = (slot.item.name or "")[:32]
    line3 = (slot.area.name or "")[:32]
    body_font = _fit_font(draw, line2 + line3, text_w, height // 6, start=int(dpi * 0.18))
    _, code_text_h = _measure(draw, slot.marker_code, code_font)
    y = code_y + code_text_h + gap
    for line in (line2, line3):
        if not line:
            continue
        draw.text((text_x, y), line, fill="black", font=body_font)
        _, h = _measure(draw, line, body_font)
        y += h + 4

    out = BytesIO()
    img.save(out, format="PNG", dpi=(dpi, dpi))
    return out.getvalue()
