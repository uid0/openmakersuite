"""Asset tag rendering service.

Generates physical asset tag images (PNG) suitable for being riveted onto
hardware. Each tag holds a QR code that links to the public scan page.
Layout reserves clear zones around the rivet hole locations so the QR
doesn't sit under the holes.
"""

from __future__ import annotations

from io import BytesIO

import qrcode
from PIL import Image

SCAN_URL_TEMPLATE = "https://dms.openmakersuite.net/scan/asset/{asset_id}"

SIZES = {
    "standard": {"width": 1.5, "height": 1.0},
    "large": {"width": 3.0, "height": 1.5},
}

RIVET_OFFSET = 0.25
RIVET_RADIUS = 0.15

DEFAULT_DPI = 1440


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

    # Safe drawing rectangle: keep design clear of the rivet exclusion circles
    # by inseting the full vertical band on each short edge.
    safe_left = int(round((RIVET_OFFSET + RIVET_RADIUS) * dpi))
    safe_right = width_px - safe_left
    safe_top = 0
    safe_bottom = height_px
    safe_width = safe_right - safe_left
    safe_height = safe_bottom - safe_top

    # QR fills the safe rect: largest square that fits within both dimensions,
    # minus a small inner padding so the code doesn't kiss the rivet zones.
    inner_padding = int(round(0.025 * dpi))
    qr_size_px = max(64, min(safe_width, safe_height) - 2 * inner_padding)

    qr_url = get_scan_url(asset)
    qr_img = _build_qr_image(qr_url, qr_size_px)

    qr_x = safe_left + (safe_width - qr_size_px) // 2
    qr_y = safe_top + (safe_height - qr_size_px) // 2
    img.paste(qr_img, (qr_x, qr_y))

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
    if size not in SIZES:
        raise InvalidTagSizeError(f"Unknown asset tag size '{size}'. Valid sizes: {sorted(SIZES)}")
    spec = SIZES[size]
    width_px = int(round(spec["width"] * dpi))
    height_px = int(round(spec["height"] * dpi))
    safe_left = int(round((RIVET_OFFSET + RIVET_RADIUS) * dpi))
    safe_right = width_px - safe_left
    return (safe_left, 0, safe_right, height_px)


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
]
