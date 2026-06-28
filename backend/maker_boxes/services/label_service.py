"""Label rendering for maker box stickers.

Produces a 3.5"x2" business-card-sized PNG (600 DPI by default) with the
member's name on the right and a left column carrying a QR code (WHO/owner
link) and, when the box has an active AprilTag allocation, a stacked
AprilTag (WHERE fiducial) beneath it. Same conventions as
:mod:`inventory.services.asset_tag_service` so the production print pipeline
is familiar.

The AprilTag is the per-box location fiducial a future vision system reads
to auto-track where each bin sits; boxes without an allocation (manual
labels, pre-conversion rows) render QR-only and unchanged.

QR payload semantics
--------------------

For a converted box (has both ``bin_id`` and ``assigned_username``) we
encode the full scanner URL so a phone camera resolves directly to
the post-conversion verification screen::

    {FRONTEND_URL}/scan/makerbox/<bin_id>/<username>/

For manual / pre-conversion / unassigned rows we fall back to the
plain ``assigned_username`` (or ``"unassigned"``) — those labels can't
self-route because the bin_id hasn't been allocated yet.
"""

from __future__ import annotations

from io import BytesIO
from typing import Iterable, Optional
from urllib.parse import quote

from django.conf import settings

import qrcode
from PIL import Image, ImageDraw, ImageFont

from fiducials.services.allocator import get_active_assignment
from fiducials.services.apriltag_render import build_apriltag_image

LABEL_WIDTH_INCHES = 3.5
LABEL_HEIGHT_INCHES = 2.0
DEFAULT_DPI = 600
INNER_MARGIN_INCHES = 0.1
# QR (WHO/owner link) shrank from 1.6" to make room for the AprilTag
# (WHERE fiducial) stacked beneath it in the left column. Both fiducials
# plus the stack gap fit the card's ~1.8" usable height:
# 0.95 + 0.05 + 0.8 = 1.8".
QR_SIZE_INCHES = 0.95
TAG_SIZE_INCHES = 0.8
TAG_STACK_GAP_INCHES = 0.05
NAME_GAP_INCHES = 0.15


def _try_font(size: int) -> ImageFont.ImageFont:
    """Return the largest available bundled TrueType font, or PIL's default."""
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


def _qr_payload(bin_id: str, username: str) -> str:
    """Decide what to encode in the QR for this row.

    Fully assigned rows (bin_id + username) get the self-resolving
    scanner URL. Everything else falls back to the username (or
    ``"unassigned"`` for blank rows) so the QR still scans to *something*
    legible — old behavior, preserved for manual labels and the
    pre-conversion queue's eventual reprint flow.
    """
    if bin_id and username:
        base = (getattr(settings, "FRONTEND_URL", "") or "").rstrip("/")
        return f"{base}/scan/makerbox/{quote(bin_id, safe='')}/{quote(username, safe='')}/"
    return username or "unassigned"


def _build_qr_image(value: str, target_px: int) -> Image.Image:
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=1,
    )
    qr.add_data(value)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    return img.resize((target_px, target_px), Image.Resampling.NEAREST)


def _measure_text(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont
) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _fit_font(
    draw: ImageDraw.ImageDraw, text: str, max_width: int, max_height: int, start_size: int
) -> ImageFont.ImageFont:
    """Shrink the font until ``text`` fits the available rectangle."""
    size = max(12, start_size)
    while size > 12:
        font = _try_font(size)
        width, height = _measure_text(draw, text, font)
        if width <= max_width and height <= max_height:
            return font
        size -= 4
    return _try_font(12)


def render_box_image(
    maker_box,
    *,
    dpi: int = DEFAULT_DPI,
    username_override: Optional[str] = None,
    first_name_override: Optional[str] = None,
    last_name_override: Optional[str] = None,
) -> Image.Image:
    """Render the per-bin label as an in-memory PIL image.

    Same args as :func:`render_box_label`. Split out so the Avery
    multi-up sheet renderer can composite without round-tripping PNG
    bytes through PIL.Image.open for every card.
    """
    username = (
        username_override
        if username_override is not None
        else getattr(maker_box, "assigned_username", "") or ""
    )
    first_name = (
        first_name_override
        if first_name_override is not None
        else getattr(maker_box, "first_name", "") or ""
    )
    last_name = (
        last_name_override
        if last_name_override is not None
        else getattr(maker_box, "last_name", "") or ""
    )

    width_px = int(round(LABEL_WIDTH_INCHES * dpi))
    height_px = int(round(LABEL_HEIGHT_INCHES * dpi))
    margin_px = int(round(INNER_MARGIN_INCHES * dpi))
    qr_px = int(round(QR_SIZE_INCHES * dpi))
    gap_px = int(round(NAME_GAP_INCHES * dpi))

    img = Image.new("RGB", (width_px, height_px), "white")
    draw = ImageDraw.Draw(img)

    # Left column: QR (WHO/owner link) on top, optional AprilTag (WHERE
    # fiducial) beneath, the pair vertically centered. tag_id is None for
    # manual / unsaved rows or rows without an allocation -> QR-only,
    # vertically centered exactly as before.
    bin_id = getattr(maker_box, "bin_id", "") or ""
    tag = get_active_assignment(maker_box) if maker_box is not None else None

    qr_x = margin_px
    qr_img = _build_qr_image(_qr_payload(bin_id, username), qr_px)
    if tag is not None:
        tag_px = int(round(TAG_SIZE_INCHES * dpi))
        stack_gap_px = int(round(TAG_STACK_GAP_INCHES * dpi))
        stack_h = qr_px + stack_gap_px + tag_px
        qr_y = (height_px - stack_h) // 2
        img.paste(qr_img, (qr_x, qr_y))
        # Center the (narrower) tag under the QR within the column width.
        tag_img = build_apriltag_image(tag.tag_id, tag_px, family=tag.family)
        tag_x = qr_x + (qr_px - tag_px) // 2
        tag_y = qr_y + qr_px + stack_gap_px
        img.paste(tag_img, (tag_x, tag_y))
    else:
        qr_y = (height_px - qr_px) // 2
        img.paste(qr_img, (qr_x, qr_y))

    text_left = qr_x + qr_px + gap_px
    text_right = width_px - margin_px
    text_area_w = max(text_right - text_left, 1)
    text_area_h = height_px - 2 * margin_px

    first_line = first_name.strip()
    last_line = last_name.strip()
    name_lines = [line for line in (first_line, last_line) if line]
    if not name_lines:
        name_lines = [username or ""]

    line_height_budget = text_area_h // max(len(name_lines), 1)
    fonts = [
        _fit_font(draw, line, text_area_w, line_height_budget, start_size=int(dpi * 0.45))
        for line in name_lines
    ]

    measurements = [_measure_text(draw, line, font) for line, font in zip(name_lines, fonts)]
    total_text_height = sum(h for _, h in measurements)
    cursor_y = (height_px - total_text_height) // 2
    for line, font, (_line_w, line_h) in zip(name_lines, fonts, measurements):
        draw.text((text_left, cursor_y), line, fill="black", font=font)
        cursor_y += line_h

    return img


def render_box_label(
    maker_box,
    *,
    dpi: int = DEFAULT_DPI,
    username_override: Optional[str] = None,
    first_name_override: Optional[str] = None,
    last_name_override: Optional[str] = None,
) -> bytes:
    """Render a personal-storage-bin label PNG and return raw bytes.

    ``maker_box`` may be a :class:`MakerBox` instance or any object that
    exposes ``assigned_username`` / ``first_name`` / ``last_name``. Optional
    overrides let the manual-create flow bypass the model.
    """
    img = render_box_image(
        maker_box,
        dpi=dpi,
        username_override=username_override,
        first_name_override=first_name_override,
        last_name_override=last_name_override,
    )
    buffer = BytesIO()
    img.save(buffer, format="PNG", dpi=(dpi, dpi))
    return buffer.getvalue()


__all__ = [
    "DEFAULT_DPI",
    "LABEL_WIDTH_INCHES",
    "LABEL_HEIGHT_INCHES",
    "render_box_image",
    "render_box_label",
]
