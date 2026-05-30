"""Server-side PNG renderer for XIAO 7.5" ePaper PM displays.

The XIAO 7.5" panel resolution is 800x480 monochrome (Seeed Studio
Wio E-Paper 7.5"). The firmware fetches the rendered PNG over HTTPS
on wake-up, flashes the panel, and sleeps — no client-side layout.
Server-side render means the panel itself stays simple, fonts come
from one place, and ops can preview the exact image a given asset
will display without firmware in hand.

Layout (800x480, top-to-bottom):

    PREVENTIVE MAINTENANCE          ← eyebrow, small bold, under a rule
    ASSET NAME                      ← bold, auto-fit, wraps to 2 lines
    PM task name                    ← regular, medium
    ┌──────────────────┐  ┌──────┐
    │  12 DAYS         │  │  QR  │  ← status headline (inverted when
    │  REMAINING       │  │      │     overdue/never) + scan-to-log QR
    └──────────────────┘  └──────┘
    Last serviced: DATE · TAG       ← footer rule + meta

The render is deterministic for a given snapshot of an asset's PM
state, so we expose ``compute_snapshot_etag`` for the HTTP layer to
short-circuit unchanged requests with 304 Not Modified.
"""

from __future__ import annotations

import functools
import hashlib
import io
import os
from typing import Iterable

from django.utils import timezone

from PIL import Image, ImageDraw, ImageFont

from inventory.models import Asset, MaintenanceItem

EPAPER_WIDTH = 800
EPAPER_HEIGHT = 480
_BG = 255  # white
_FG = 0  # black

_MARGIN = 34

# DejaVu ships in the slim Debian base the worker container builds on.
# If a future base image drops it, ``_font`` falls back to Pillow's
# scalable built-in so the panel degrades to plain-but-legible rather
# than crashing the device's wake cycle.
_FONT_DIR = "/usr/share/fonts/truetype/dejavu"
_FONT_FILES = {
    "sans": "DejaVuSans.ttf",
    "sans_bold": "DejaVuSans-Bold.ttf",
    "mono_bold": "DejaVuSansMono-Bold.ttf",
}


@functools.lru_cache(maxsize=64)
def _font(kind: str, size: int) -> ImageFont.ImageFont:
    path = os.path.join(_FONT_DIR, _FONT_FILES.get(kind, "DejaVuSans.ttf"))
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:  # Pillow < 10.1 has no size kwarg
            return ImageFont.load_default()


# ---------------------------------------------------------------------------
# PM-state selection + ETag (unchanged contract)
# ---------------------------------------------------------------------------


# A maintenance task counts as "preventive" (panel-eligible) when it
# recurs — i.e. it has an interval. One-off / as-needed items
# (interval_days is None) are left off the panel.
_WARNING_FRACTION = 0.2  # final 20% of the interval reads as "due soon"

# Panel status buckets, most urgent first.
_OVERDUE, _NEVER, _WARNING, _OK = "overdue", "never", "warning", "ok"


def _recurring_items(asset: Asset) -> list[MaintenanceItem]:
    """Active maintenance items that recur (= preventive)."""
    return [item for item in asset.maintenance_items.filter(is_active=True) if item.interval_days]


def _days_until_due(item: MaintenanceItem) -> int | None:
    """Whole days until the item is next due; None if never completed."""
    due = item.next_due_at
    if due is None:
        return None
    return (due.date() - timezone.now().date()).days


def _item_status(item: MaintenanceItem) -> str:
    if item.last_completed_at is None:
        return _NEVER
    days = _days_until_due(item)
    if days is None:
        return _NEVER
    if days < 0:
        return _OVERDUE
    if item.interval_days and days <= max(1, int(item.interval_days * _WARNING_FRACTION)):
        return _WARNING
    return _OK


def _next_due_item(asset: Asset) -> MaintenanceItem | None:
    """Pick the recurring task that should drive the panel display.

    Priority: overdue (most overdue first) → never completed → due soon →
    ok (fewest days remaining). Returns ``None`` when the asset has no
    active recurring maintenance items.
    """
    items = _recurring_items(asset)
    if not items:
        return None

    order = {_OVERDUE: 0, _NEVER: 1, _WARNING: 2, _OK: 3}

    def sort_key(item: MaintenanceItem) -> tuple[int, int]:
        days = _days_until_due(item)
        # Never-completed sorts to the front of its bucket; within overdue
        # the most-negative (most overdue) day count comes first.
        return (order[_item_status(item)], days if days is not None else -10_000)

    return sorted(items, key=sort_key)[0]


def _snapshot_fingerprint(asset: Asset, items: Iterable[MaintenanceItem]) -> str:
    """Return a stable hex fingerprint of the inputs to the rendered image."""
    parts: list[str] = [str(asset.pk), asset.name]
    for item in items:
        last = item.last_completed_at.isoformat() if item.last_completed_at else "never"
        parts.append("|".join([str(item.pk), item.title, str(item.interval_days), last]))
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return digest[:32]


def compute_snapshot_etag(asset: Asset) -> str:
    """Public helper for the view layer: stable ETag for an asset's PM state."""
    items = sorted(_recurring_items(asset), key=lambda item: str(item.pk))
    return _snapshot_fingerprint(asset, items)


def _status_line(item: MaintenanceItem | None) -> str:
    if item is None:
        return "NO PM TASKS"
    if item.last_completed_at is None:
        return "DUE NOW"
    days = _days_until_due(item)
    if days is None:
        return "DUE NOW"
    if days < 0:
        return f"OVERDUE BY {abs(days)} DAYS"
    return f"{days} DAYS REMAINING"


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------


def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _wrap(draw, text: str, font, max_width: int, max_lines: int) -> list[str]:
    """Greedy word-wrap; the final line is ellipsised if it overflows."""
    words = text.split()
    lines: list[str] = []
    cur = ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if _text_size(draw, trial, font)[0] <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
            if len(lines) == max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) == max_lines and words:
        # Make sure the last kept line plus any dropped words is marked.
        joined = " ".join(lines)
        if _text_size(draw, joined, font)[0] < _text_size(draw, text, font)[0]:
            while lines[-1] and _text_size(draw, lines[-1] + "…", font)[0] > max_width:
                lines[-1] = lines[-1][:-1]
            lines[-1] = lines[-1].rstrip() + "…"
    return lines or [""]


def _fit_font(draw, text: str, kind: str, max_size: int, min_size: int, max_width: int):
    """Largest font of ``kind`` at which ``text`` fits on one line."""
    size = max_size
    while size > min_size:
        font = _font(kind, size)
        if _text_size(draw, text, font)[0] <= max_width:
            return font
        size -= 2
    return _font(kind, min_size)


def _qr_image(data: str, target_px: int) -> Image.Image:
    """Render ``data`` to a crisp monochrome QR of ~``target_px`` square."""
    import qrcode

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("L")
    # NEAREST keeps the module edges hard so the panel threshold is clean.
    return img.resize((target_px, target_px), Image.NEAREST)


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------


def render_pm_image(asset: Asset, *, service_url: str | None = None) -> bytes:
    """Render the full panel image for ``asset`` and return PNG bytes.

    ``service_url`` (when given) is encoded into a QR in the lower-right
    so a maintainer can scan the panel, see the task, and log the work.
    """
    img = Image.new("L", (EPAPER_WIDTH, EPAPER_HEIGHT), color=_BG)
    draw = ImageDraw.Draw(img)
    right = EPAPER_WIDTH - _MARGIN

    # Frame.
    draw.rectangle([(5, 5), (EPAPER_WIDTH - 6, EPAPER_HEIGHT - 6)], outline=_FG, width=4)

    item = _next_due_item(asset)
    status = _item_status(item) if item else None
    urgent = status in (_OVERDUE, _NEVER)

    # QR block (lower-right) — reserve its column so text wraps clear of it.
    qr_px = 188
    qr_x = right - qr_px
    qr_y = EPAPER_HEIGHT - _MARGIN - 26 - qr_px
    has_qr = bool(service_url)
    text_right = (qr_x - 24) if has_qr else right

    # Eyebrow + rule.
    eyebrow = _font("mono_bold", 24)
    draw.text((_MARGIN, _MARGIN - 8), "PREVENTIVE MAINTENANCE", font=eyebrow, fill=_FG)
    rule_y = _MARGIN + 26
    draw.line([(_MARGIN, rule_y), (right, rule_y)], fill=_FG, width=2)

    # Asset name — the "what is this" line. Auto-fit, wrap to 2 lines.
    name = (asset.name or "UNNAMED ASSET").upper()
    name_font = _fit_font(draw, name, "sans_bold", 52, 30, EPAPER_WIDTH - 2 * _MARGIN)
    name_lines = _wrap(draw, name, name_font, EPAPER_WIDTH - 2 * _MARGIN, max_lines=2)
    y = rule_y + 18
    for line in name_lines:
        draw.text((_MARGIN, y), line, font=name_font, fill=_FG)
        y += _text_size(draw, line, name_font)[1] + 8

    # PM task.
    task_font = _font("sans", 30)
    task_line = item.title if item else "No preventive tasks"
    for line in _wrap(draw, task_line, task_font, text_right - _MARGIN, max_lines=2):
        draw.text((_MARGIN, y), line, font=task_font, fill=_FG)
        y += _text_size(draw, line, task_font)[1] + 6

    # Status headline — the glanceable number, in a box. Inverted for
    # overdue/never so a problem panel reads across the room.
    status_text = _status_line(item)
    box_top = max(y + 14, qr_y)
    box_bottom = qr_y + qr_px
    box_left = _MARGIN
    box_right = text_right
    status_font = _fit_font(draw, status_text, "sans_bold", 60, 26, (box_right - box_left) - 36)
    sw, sh = _text_size(draw, status_text, status_font)
    cx = box_left + (box_right - box_left) // 2
    cy = box_top + (box_bottom - box_top) // 2
    if urgent:
        draw.rectangle([(box_left, box_top), (box_right, box_bottom)], fill=_FG)
        draw.text((cx - sw // 2, cy - sh // 2 - 6), status_text, font=status_font, fill=_BG)
    else:
        draw.rectangle([(box_left, box_top), (box_right, box_bottom)], outline=_FG, width=3)
        draw.text((cx - sw // 2, cy - sh // 2 - 6), status_text, font=status_font, fill=_FG)

    # QR + caption.
    if has_qr:
        img.paste(_qr_image(service_url, qr_px), (qr_x, qr_y))
        cap = _font("mono_bold", 19)
        for i, line in enumerate(("SCAN TO", "LOG SERVICE")):
            cw = _text_size(draw, line, cap)[0]
            draw.text(
                (qr_x + (qr_px - cw) // 2, qr_y + qr_px + 2 + i * 22),
                line,
                font=cap,
                fill=_FG,
            )

    # Footer.
    foot_y = EPAPER_HEIGHT - _MARGIN - 18
    draw.line([(_MARGIN, foot_y - 8), (right, foot_y - 8)], fill=_FG, width=1)
    last_line = "Last serviced: never"
    if item and item.last_completed_at:
        last_line = f"Last serviced: {item.last_completed_at.date().isoformat()}"
    tag = getattr(asset, "asset_tag", "") or ""
    if tag:
        last_line = f"{last_line}   ·   {tag}"
    draw.text((_MARGIN, foot_y), last_line, font=_font("sans", 19), fill=_FG)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
