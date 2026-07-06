"""Asset tag rendering service.

Generates physical asset tag images (PNG) suitable for being riveted onto
hardware. Each tag holds a QR code that links to the public scan page, with
the human-readable asset tag string (e.g. ``DMS-YYANNNSS``) printed directly
beneath the QR so a tag is identifiable by eye without scanning. Layout
reserves clear zones around the rivet hole locations so nothing sits under
the holes.
"""

from __future__ import annotations

from io import BytesIO

import qrcode
from PIL import Image, ImageDraw, ImageFont

SCAN_URL_TEMPLATE = "https://dms.openmakersuite.net/scan/asset/{asset_id}"

SIZES = {
    "standard": {"width": 1.5, "height": 1.0},
    "large": {"width": 3.0, "height": 1.5},
}

RIVET_OFFSET = 0.25
RIVET_RADIUS = 0.15

DEFAULT_DPI = 1440

# Fraction of the tag height reserved for the printed asset-tag text band that
# sits beneath the QR. The asset tag is a single fixed-width line (DMS-YYANNNSS,
# ~12 chars), so a modest band keeps the QR large while staying readable.
TEXT_BAND_FRACTION = 0.13

# Gap between the QR and the text band, as a fraction of tag height.
TEXT_GAP_FRACTION = 0.025

# TrueType fonts tried, in order, for the printed tag text. This mirrors the
# sans style of the Brother PDF label (Helvetica); DejaVu Sans is the closest
# font shipped on the print/CI hosts (same path used by the ESC-P renderer).
# Falls back to PIL's built-in bitmap font if none of these can be loaded.
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


class InvalidTagSizeError(ValueError):
    """Raised when an unknown size is requested."""


def get_scan_url(asset) -> str:
    return SCAN_URL_TEMPLATE.format(asset_id=asset.id)


def _build_qr_image(url: str, target_px: int) -> Image.Image:
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=1,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    return img.resize((target_px, target_px), Image.Resampling.NEAREST)


def _compute_layout(size: str, dpi: int) -> dict:
    """Compute the pixel geometry of a tag: safe rect, QR box, and text box.

    Geometry depends only on ``size`` and ``dpi`` (not on the asset), so the
    QR box and text box are deterministic and can be introspected by callers
    and tests. The QR is horizontally centered in the safe rect with the text
    band directly beneath it; the whole block is centered vertically.
    """
    if size not in SIZES:
        raise InvalidTagSizeError(f"Unknown asset tag size '{size}'. Valid sizes: {sorted(SIZES)}")

    spec = SIZES[size]
    width_px = int(round(spec["width"] * dpi))
    height_px = int(round(spec["height"] * dpi))

    # Safe drawing rectangle: keep design clear of the rivet exclusion circles
    # by insetting the full vertical band on each short edge.
    safe_left = int(round((RIVET_OFFSET + RIVET_RADIUS) * dpi))
    safe_right = width_px - safe_left
    safe_top = 0
    safe_bottom = height_px
    safe_width = safe_right - safe_left
    safe_height = safe_bottom - safe_top

    # Small inner padding so content doesn't kiss the rivet zones / edges.
    inner_padding = int(round(0.025 * dpi))
    gap = int(round(TEXT_GAP_FRACTION * spec["height"] * dpi))
    text_band = int(round(TEXT_BAND_FRACTION * spec["height"] * dpi))

    # QR is the largest square fitting the safe width and the height left over
    # after reserving the text band + gap. On the standard tag this is still
    # width-limited (QR unchanged); on the taller/large tag the QR shrinks
    # slightly to make room for the text while staying well within scan range.
    avail_height = safe_height - 2 * inner_padding - gap - text_band
    qr_size = max(64, min(safe_width - 2 * inner_padding, avail_height))

    # Center the QR + gap + text block vertically within the safe rect.
    content_height = qr_size + gap + text_band
    content_top = safe_top + max(inner_padding, (safe_height - content_height) // 2)

    qr_x = safe_left + (safe_width - qr_size) // 2
    qr_y = content_top

    text_left = safe_left + inner_padding
    text_right = safe_right - inner_padding
    text_top = qr_y + qr_size + gap
    text_bottom = text_top + text_band

    return {
        "width_px": width_px,
        "height_px": height_px,
        "safe": (safe_left, safe_top, safe_right, safe_bottom),
        "qr": (qr_x, qr_y, qr_x + qr_size, qr_y + qr_size),
        "qr_size": qr_size,
        "text": (text_left, text_top, text_right, text_bottom),
    }


def _load_font(size_px: int) -> ImageFont.ImageFont:
    """Load the tag text font at ``size_px``, falling back to the default."""
    size_px = max(1, int(size_px))
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size_px)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit_font(draw: ImageDraw.ImageDraw, text: str, max_width: int, max_height: int):
    """Return the largest available font whose rendered ``text`` fits the box.

    Starts at a font size equal to the band height (bounding the height) and
    scales down proportionally if the text is wider than ``max_width``.
    """
    size_px = max(1, int(max_height))
    font = _load_font(size_px)
    left, _, right, _ = draw.textbbox((0, 0), text, font=font)
    text_width = right - left
    if text_width > max_width and text_width > 0:
        size_px = max(1, int(size_px * max_width / text_width))
        font = _load_font(size_px)
    return font


def _draw_tag_text(img: Image.Image, text: str, box: tuple[int, int, int, int]) -> None:
    """Draw ``text`` centered within ``box`` (left, top, right, bottom)."""
    left, top, right, bottom = box
    max_width = right - left
    max_height = bottom - top
    if max_width <= 0 or max_height <= 0:
        return

    draw = ImageDraw.Draw(img)
    font = _fit_font(draw, text, max_width, max_height)

    # Center on the ink bounding box so bearings/descenders don't skew it.
    ink_left, ink_top, ink_right, ink_bottom = draw.textbbox((0, 0), text, font=font)
    text_width = ink_right - ink_left
    text_height = ink_bottom - ink_top
    cx = (left + right) // 2
    cy = (top + bottom) // 2
    draw_x = cx - text_width // 2 - ink_left
    draw_y = cy - text_height // 2 - ink_top
    draw.text((draw_x, draw_y), text, fill="black", font=font)


def render_asset_tag(asset, size: str = "standard", dpi: int = DEFAULT_DPI) -> bytes:
    """Render an asset tag PNG and return the raw bytes.

    The tag holds a QR code (encoding the asset ``id`` scan URL) with the
    human-readable ``asset_tag`` string printed directly beneath it. Assets
    without an ``asset_tag`` render the QR only.

    Args:
        asset: Asset instance (must expose ``id`` UUID; may expose ``asset_tag``).
        size: One of the keys in :data:`SIZES`.
        dpi: Output resolution. Defaults to 1440 DPI.

    Returns:
        PNG-encoded bytes of the rendered tag.
    """
    layout = _compute_layout(size, dpi)

    img = Image.new("RGB", (layout["width_px"], layout["height_px"]), "white")

    qr_left, qr_top, _, _ = layout["qr"]
    qr_url = get_scan_url(asset)
    qr_img = _build_qr_image(qr_url, layout["qr_size"])
    img.paste(qr_img, (qr_left, qr_top))

    tag_text = (getattr(asset, "asset_tag", "") or "").strip()
    if tag_text:
        _draw_tag_text(img, tag_text, layout["text"])

    buffer = BytesIO()
    # Pillow records DPI in metadata so downstream printers honor it.
    img.save(buffer, format="PNG", dpi=(dpi, dpi))
    return buffer.getvalue()


def get_rivet_centers_px(size: str, dpi: int = DEFAULT_DPI) -> list[tuple[int, int]]:
    """Return the pixel coordinates of the two rivet hole centers.

    Useful for tests and any caller that wants to verify exclusion zones.
    """
    if size not in SIZES:
        raise InvalidTagSizeError(f"Unknown asset tag size '{size}'. Valid sizes: {sorted(SIZES)}")
    spec = SIZES[size]
    width_px = int(round(spec["width"] * dpi))
    height_px = int(round(spec["height"] * dpi))
    cx_left = int(round(RIVET_OFFSET * dpi))
    cx_right = width_px - cx_left
    cy = height_px // 2
    return [(cx_left, cy), (cx_right, cy)]


def get_safe_rect_px(size: str, dpi: int = DEFAULT_DPI) -> tuple[int, int, int, int]:
    """Return the (left, top, right, bottom) pixel bounds of the safe drawing rect."""
    return _compute_layout(size, dpi)["safe"]


def get_qr_box_px(size: str, dpi: int = DEFAULT_DPI) -> tuple[int, int, int, int]:
    """Return the (left, top, right, bottom) pixel box the QR is pasted into."""
    return _compute_layout(size, dpi)["qr"]


def get_text_box_px(size: str, dpi: int = DEFAULT_DPI) -> tuple[int, int, int, int]:
    """Return the (left, top, right, bottom) pixel box the asset-tag text fills."""
    return _compute_layout(size, dpi)["text"]


__all__ = [
    "SIZES",
    "RIVET_OFFSET",
    "RIVET_RADIUS",
    "DEFAULT_DPI",
    "SCAN_URL_TEMPLATE",
    "InvalidTagSizeError",
    "render_asset_tag",
    "get_scan_url",
    "get_rivet_centers_px",
    "get_safe_rect_px",
    "get_qr_box_px",
    "get_text_box_px",
]
