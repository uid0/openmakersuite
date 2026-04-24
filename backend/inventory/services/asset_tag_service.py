"""Asset tag rendering service.

Generates physical asset tag images (PNG) suitable for being riveted onto
hardware. Each tag includes a QR code that links to the public scan page,
plus a short call-to-action. Layout reserves clear zones around the rivet
hole locations so design and QR elements don't sit under the holes.
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

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
)


class InvalidTagSizeError(ValueError):
    """Raised when an unknown size is requested."""


def get_scan_url(asset) -> str:
    return SCAN_URL_TEMPLATE.format(asset_id=asset.id)


def _load_font(size_px: int) -> ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size_px)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit_font(
    draw: ImageDraw.ImageDraw, lines, max_width_px: int, max_height_px: int, start_size: int
) -> ImageFont.ImageFont:
    """Find the largest font size at which all lines fit the given box."""
    size = start_size
    while size > 8:
        font = _load_font(size)
        line_heights = []
        max_line_w = 0
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
            line_heights.append(h)
            if w > max_line_w:
                max_line_w = w
        line_gap = int(size * 0.25)
        total_h = sum(line_heights) + line_gap * (len(lines) - 1)
        if max_line_w <= max_width_px and total_h <= max_height_px:
            return font
        size -= 2
    return _load_font(size)


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


def render_asset_tag(asset, size: str = "standard", dpi: int = DEFAULT_DPI) -> bytes:
    """Render an asset tag PNG and return the raw bytes.

    Args:
        asset: Asset instance (must expose ``id`` UUID).
        size: One of the keys in :data:`SIZES`.
        dpi: Output resolution. Defaults to 1440 DPI.

    Returns:
        PNG-encoded bytes of the rendered tag.
    """
    if size not in SIZES:
        raise InvalidTagSizeError(f"Unknown asset tag size '{size}'. Valid sizes: {sorted(SIZES)}")

    spec = SIZES[size]
    width_px = int(round(spec["width"] * dpi))
    height_px = int(round(spec["height"] * dpi))

    img = Image.new("RGB", (width_px, height_px), "white")
    draw = ImageDraw.Draw(img)

    # Safe drawing rectangle: keep design clear of the rivet exclusion circles
    # by inseting the full vertical band on each short edge.
    safe_left = int(round((RIVET_OFFSET + RIVET_RADIUS) * dpi))
    safe_right = width_px - safe_left
    safe_top = 0
    safe_bottom = height_px
    safe_width = safe_right - safe_left
    safe_height = safe_bottom - safe_top

    # QR sizing: square that fits within safe height (with a little padding)
    # while leaving roughly 40% of safe width for the CTA text.
    inner_padding = int(round(0.05 * dpi))
    max_qr_by_height = safe_height - 2 * inner_padding
    max_qr_by_width = int(safe_width * 0.6)
    qr_size_px = max(64, min(max_qr_by_height, max_qr_by_width))

    qr_url = get_scan_url(asset)
    qr_img = _build_qr_image(qr_url, qr_size_px)

    qr_x = safe_left
    qr_y = safe_top + (safe_height - qr_size_px) // 2
    img.paste(qr_img, (qr_x, qr_y))

    # CTA text occupies the remainder of the safe rect to the right of the QR.
    text_x = qr_x + qr_size_px + inner_padding
    text_box_w = safe_right - text_x
    text_box_h = safe_height - 2 * inner_padding

    if text_box_w > 0:
        lines = ["Problems?", "Scan me!"]
        # Start with a generous font size proportional to the tag height,
        # then shrink to fit.
        start_size = max(24, int(height_px * 0.18))
        font = _fit_font(draw, lines, text_box_w, text_box_h, start_size)

        line_metrics = []
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            line_metrics.append((line, bbox[2] - bbox[0], bbox[3] - bbox[1]))
        line_gap = int(font.size * 0.25)
        total_h = sum(h for _, _, h in line_metrics) + line_gap * (len(line_metrics) - 1)

        cursor_y = safe_top + (safe_height - total_h) // 2
        for line, line_w, line_h in line_metrics:
            cursor_x = text_x + (text_box_w - line_w) // 2
            draw.text((cursor_x, cursor_y), line, fill="black", font=font)
            cursor_y += line_h + line_gap

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
]
