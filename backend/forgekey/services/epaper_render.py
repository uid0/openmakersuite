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

from inventory.models import Asset, AssetOutOfService, AssetReservation, MaintenanceItem

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


def _required_cert_names(asset: Asset) -> list[str]:
    """Active required certifications, name-only, alpha-sorted for stable
    rendering + etag. Empty list when none are wired."""
    rel = getattr(asset, "required_certifications", None)
    if rel is None:
        return []
    return sorted(rel.filter(is_active=True).values_list("name", flat=True))


def _snapshot_fingerprint(asset: Asset, items: Iterable[MaintenanceItem]) -> str:
    """Return a stable hex fingerprint of the inputs to the rendered image.

    Includes ``today`` because the rendered PNG carries day-level
    derivations (``X DAYS REMAINING``, the OVERDUE/WARNING/OK bucket
    that drives the inverted-box treatment). Without this, a panel
    whose underlying ``MaintenanceItem.last_completed_at`` hasn't
    changed since yesterday would keep getting 304s and display a
    stale day count forever — the firmware wake cycle hits the
    endpoint hourly, sees the unchanged ETag, and keeps the existing
    paint. Folding the date in forces at most one fresh render per
    UTC day, which is the natural granularity of what's displayed.
    """
    parts: list[str] = [
        str(asset.pk),
        asset.name,
        f"training={int(bool(getattr(asset, 'training_required', False)))}",
        "certs=" + "|".join(_required_cert_names(asset)),
        f"date={timezone.now().date().isoformat()}",
    ]
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

    # Training-required badge — inverted pill in the right of the eyebrow
    # row when the asset is gated on operator training. Drawn before the
    # rule so the pill bottom aligns with it; readable across the shop
    # because of the inverse contrast against the eyebrow text. When
    # the asset has specific required_certifications wired, the pill
    # shows the cert names ("REQ: WELD-1") instead of the generic
    # TRAINING REQUIRED text, so an operator knows the exact card to
    # present. Multiple certs are joined with " · " and auto-fit; on
    # an 800px panel ~3 short cert names is the realistic ceiling
    # before the badge starts crowding the eyebrow.
    cert_names = _required_cert_names(asset)
    needs_badge = bool(cert_names) or bool(getattr(asset, "training_required", False))
    if needs_badge:
        max_badge_w = (right - _MARGIN) // 2
        if cert_names:
            badge_text = "REQ: " + " · ".join(cert_names)
        else:
            badge_text = "TRAINING REQUIRED"
        badge_font = _fit_font(draw, badge_text, "mono_bold", 22, 14, max_badge_w - 2 * 12)
        bw, bh = _text_size(draw, badge_text, badge_font)
        pad_x, pad_y = 12, 4
        badge_right = right
        badge_left = badge_right - bw - 2 * pad_x
        badge_top = _MARGIN - 12
        badge_bottom = badge_top + bh + 2 * pad_y
        draw.rectangle(
            [(badge_left, badge_top), (badge_right, badge_bottom)],
            fill=_FG,
        )
        draw.text(
            (badge_left + pad_x, badge_top + pad_y - 2),
            badge_text,
            font=badge_font,
            fill=_BG,
        )

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


# ---------------------------------------------------------------------------
# Face selection — PM / reservation / OOS
# ---------------------------------------------------------------------------


FACE_PM = "pm"
FACE_RESERVATION = "reservation"
FACE_OOS = "oos"


def _current_oos(asset: Asset) -> AssetOutOfService | None:
    """Open (unrestored) OOS row for this asset, if any."""
    return (
        AssetOutOfService.objects.filter(asset=asset, restored_at__isnull=True)
        .order_by("-placed_out_at")
        .first()
    )


def _current_reservation(asset: Asset) -> AssetReservation | None:
    """Reservation whose [starts_at, ends_at) window contains now."""
    now = timezone.now()
    return (
        AssetReservation.objects.filter(
            asset=asset,
            cancelled_at__isnull=True,
            starts_at__lte=now,
            ends_at__gt=now,
        )
        .order_by("starts_at")
        .first()
    )


def _pick_face(asset: Asset, display) -> tuple[str, AssetOutOfService | AssetReservation | None]:
    """Choose which face the panel should show on this request.

    Precedence (highest first):
    1. Open OOS — preempts everything. There is no rotation; the panel
       belongs to the OOS narrative until the asset is restored.
    2. Current reservation AND eligible PM task — rotate based on the
       per-display ``event_face_weight`` / ``pm_face_weight``. The
       ``rotation_counter`` modulo the weight sum picks this fetch's
       face; the caller advances the counter after rendering so the
       next fetch picks the next face in the cycle.
    3. Current reservation only (or PM-eligibility check fails) — show
       the reservation face every wake.
    4. Default — PM face.

    Returns ``(face, source_row)`` where ``source_row`` is the OOS or
    reservation feeding the chosen face (None for PM).
    """
    oos = _current_oos(asset)
    if oos is not None:
        return FACE_OOS, oos

    reservation = _current_reservation(asset)
    pm_eligible = _next_due_item(asset) is not None

    if reservation is not None and pm_eligible:
        event_w = max(0, getattr(display, "event_face_weight", 2)) if display is not None else 2
        pm_w = max(0, getattr(display, "pm_face_weight", 1)) if display is not None else 1
        total = event_w + pm_w
        if total == 0:
            # Operator set both weights to zero; treat as "always PM" so the
            # panel still has something to draw.
            return FACE_PM, None
        counter = int(getattr(display, "rotation_counter", 0)) if display is not None else 0
        if (counter % total) < event_w:
            return FACE_RESERVATION, reservation
        return FACE_PM, None

    if reservation is not None:
        return FACE_RESERVATION, reservation

    return FACE_PM, None


# ---------------------------------------------------------------------------
# OOS + Reservation renderers
# ---------------------------------------------------------------------------


def _draw_eyebrow(draw, eyebrow_text: str, right: int, *, inverted: bool = False) -> int:
    """Render the eyebrow + rule and return the y-coordinate of the rule.

    When ``inverted`` is True, the eyebrow is drawn as a black bar with
    white text — used to make the OOS face read across the shop floor
    without anyone having to walk up.
    """
    font = _font("mono_bold", 24)
    if inverted:
        bar_top = _MARGIN - 12
        bar_bottom = _MARGIN + 22
        draw.rectangle([(_MARGIN - 10, bar_top), (right + 10, bar_bottom)], fill=_FG)
        draw.text((_MARGIN, _MARGIN - 8), eyebrow_text, font=font, fill=_BG)
        rule_y = bar_bottom + 6
    else:
        draw.text((_MARGIN, _MARGIN - 8), eyebrow_text, font=font, fill=_FG)
        rule_y = _MARGIN + 26
    draw.line([(_MARGIN, rule_y), (right, rule_y)], fill=_FG, width=2)
    return rule_y


def _draw_asset_headline(draw, name: str, rule_y: int, right: int) -> int:
    """Asset name auto-fit + wrapped to 2 lines. Returns the new y cursor."""
    name = (name or "UNNAMED ASSET").upper()
    name_font = _fit_font(draw, name, "sans_bold", 52, 30, EPAPER_WIDTH - 2 * _MARGIN)
    name_lines = _wrap(draw, name, name_font, EPAPER_WIDTH - 2 * _MARGIN, max_lines=2)
    y = rule_y + 18
    for line in name_lines:
        draw.text((_MARGIN, y), line, font=name_font, fill=_FG)
        y += _text_size(draw, line, name_font)[1] + 8
    return y


def render_oos_image(asset: Asset, oos: AssetOutOfService) -> bytes:
    """Render the OUT OF SERVICE face for ``asset``.

    Layout reflects the user's spec: when the asset was placed out, who
    placed it out, and the expected return date when known. Eyebrow is
    inverted so the alarm reads from across the room.
    """
    img = Image.new("L", (EPAPER_WIDTH, EPAPER_HEIGHT), color=_BG)
    draw = ImageDraw.Draw(img)
    right = EPAPER_WIDTH - _MARGIN

    # Thicker frame than the PM face — distinct silhouette.
    draw.rectangle([(5, 5), (EPAPER_WIDTH - 6, EPAPER_HEIGHT - 6)], outline=_FG, width=6)

    rule_y = _draw_eyebrow(draw, "OUT OF SERVICE", right, inverted=True)
    y = _draw_asset_headline(draw, asset.name, rule_y, right)

    body_font = _font("sans", 26)
    meta_font = _font("mono_bold", 22)
    y += 10

    placed_local = timezone.localtime(oos.placed_out_at).strftime("%Y-%m-%d %H:%M")
    placed_by_name = (
        oos.placed_by.get_full_name() or oos.placed_by.username
        if oos.placed_by_id is not None
        else "—"
    )
    rows = [
        ("PLACED OUT", placed_local),
        ("BY", placed_by_name),
    ]
    if oos.expected_return_at is not None:
        rows.append(
            ("EXPECTED BACK", timezone.localtime(oos.expected_return_at).strftime("%Y-%m-%d"))
        )
    else:
        rows.append(("EXPECTED BACK", "TBD"))

    label_w = max(_text_size(draw, label, meta_font)[0] for label, _ in rows)
    for label, value in rows:
        draw.text((_MARGIN, y), label, font=meta_font, fill=_FG)
        draw.text((_MARGIN + label_w + 20, y), value, font=body_font, fill=_FG)
        y += max(_text_size(draw, label, meta_font)[1], _text_size(draw, value, body_font)[1]) + 12

    # Reason — multi-line, fills remaining vertical room.
    y += 6
    draw.line([(_MARGIN, y), (right, y)], fill=_FG, width=1)
    y += 10
    reason_font = _font("sans", 24)
    reason_lines = _wrap(
        draw,
        oos.reason or "(no reason recorded)",
        reason_font,
        right - _MARGIN,
        max_lines=6,
    )
    for line in reason_lines:
        draw.text((_MARGIN, y), line, font=reason_font, fill=_FG)
        y += _text_size(draw, line, reason_font)[1] + 6

    # Footer.
    foot_y = EPAPER_HEIGHT - _MARGIN - 18
    draw.line([(_MARGIN, foot_y - 8), (right, foot_y - 8)], fill=_FG, width=1)
    tag = getattr(asset, "asset_tag", "") or ""
    footer = "Do not operate"
    if tag:
        footer = f"{footer}   ·   {tag}"
    draw.text((_MARGIN, foot_y), footer, font=_font("sans", 19), fill=_FG)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def render_reservation_image(asset: Asset, reservation: AssetReservation) -> bytes:
    """Render the RESERVED face — title, reserver, ends_at, remaining."""
    img = Image.new("L", (EPAPER_WIDTH, EPAPER_HEIGHT), color=_BG)
    draw = ImageDraw.Draw(img)
    right = EPAPER_WIDTH - _MARGIN

    draw.rectangle([(5, 5), (EPAPER_WIDTH - 6, EPAPER_HEIGHT - 6)], outline=_FG, width=4)

    rule_y = _draw_eyebrow(draw, "RESERVED", right)
    y = _draw_asset_headline(draw, asset.name, rule_y, right)

    title_font = _fit_font(draw, reservation.title, "sans_bold", 38, 22, EPAPER_WIDTH - 2 * _MARGIN)
    for line in _wrap(draw, reservation.title, title_font, EPAPER_WIDTH - 2 * _MARGIN, max_lines=2):
        draw.text((_MARGIN, y), line, font=title_font, fill=_FG)
        y += _text_size(draw, line, title_font)[1] + 6

    meta_font = _font("mono_bold", 22)
    body_font = _font("sans", 26)
    y += 14

    reserved_by_name = (
        reservation.reserved_by.get_full_name() or reservation.reserved_by.username
        if reservation.reserved_by_id is not None
        else "—"
    )
    ends_local = timezone.localtime(reservation.ends_at)
    rows = [
        ("RESERVED BY", reserved_by_name),
        ("ENDS", ends_local.strftime("%Y-%m-%d %H:%M")),
    ]
    label_w = max(_text_size(draw, label, meta_font)[0] for label, _ in rows)
    for label, value in rows:
        draw.text((_MARGIN, y), label, font=meta_font, fill=_FG)
        draw.text((_MARGIN + label_w + 20, y), value, font=body_font, fill=_FG)
        y += max(_text_size(draw, label, meta_font)[1], _text_size(draw, value, body_font)[1]) + 10

    # Time remaining — big glanceable number, mirror of PM's status box.
    remaining = ends_local - timezone.localtime()
    minutes_left = int(remaining.total_seconds() // 60)
    if minutes_left < 0:
        time_text = "ENDED"
    elif minutes_left < 60:
        time_text = f"{minutes_left}m LEFT"
    elif minutes_left < 24 * 60:
        time_text = f"{minutes_left // 60}h {minutes_left % 60:02d}m LEFT"
    else:
        time_text = f"{minutes_left // (24 * 60)}d LEFT"

    box_top = max(y + 14, EPAPER_HEIGHT - _MARGIN - 130)
    box_bottom = EPAPER_HEIGHT - _MARGIN - 36
    box_left = _MARGIN
    box_right = right
    status_font = _fit_font(draw, time_text, "sans_bold", 60, 26, (box_right - box_left) - 36)
    sw, sh = _text_size(draw, time_text, status_font)
    cx = box_left + (box_right - box_left) // 2
    cy = box_top + (box_bottom - box_top) // 2
    draw.rectangle([(box_left, box_top), (box_right, box_bottom)], outline=_FG, width=3)
    draw.text((cx - sw // 2, cy - sh // 2 - 6), time_text, font=status_font, fill=_FG)

    foot_y = EPAPER_HEIGHT - _MARGIN - 18
    draw.line([(_MARGIN, foot_y - 8), (right, foot_y - 8)], fill=_FG, width=1)
    tag = getattr(asset, "asset_tag", "") or ""
    footer_left = f"Started {timezone.localtime(reservation.starts_at).strftime('%Y-%m-%d %H:%M')}"
    if tag:
        footer_left = f"{footer_left}   ·   {tag}"
    draw.text((_MARGIN, foot_y), footer_left, font=_font("sans", 19), fill=_FG)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Top-level entry point + multi-face etag
# ---------------------------------------------------------------------------


def compute_display_etag(asset: Asset, display) -> str:
    """Etag that covers the chosen face, not just the PM snapshot.

    Includes (face, source-row pk, OOS pk, reservation pk, plus the
    existing PM fingerprint). The rotation slot is only folded in when
    rotation actually competes — i.e. both a current reservation AND
    an eligible PM are present — so single-face panels still 304 on
    repeat fetches even though the counter advances server-side.
    """
    face, source = _pick_face(asset, display)

    pm_part = compute_snapshot_etag(asset)
    parts: list[str] = [face, pm_part]
    if source is not None:
        parts.append(str(source.pk))
        if face == FACE_OOS:
            parts.append(source.updated_at.isoformat())
        elif face == FACE_RESERVATION:
            parts.append(source.ends_at.isoformat())
            # Bucket the time remaining to ~5-minute precision so the
            # "time left" headline updates without thrashing every minute.
            now = timezone.now()
            buckets_left = max(0, int((source.ends_at - now).total_seconds() // 300))
            parts.append(str(buckets_left))
    if display is not None and _rotation_competes(asset):
        event_w = max(0, getattr(display, "event_face_weight", 2))
        pm_w = max(0, getattr(display, "pm_face_weight", 1))
        counter = int(getattr(display, "rotation_counter", 0))
        total = event_w + pm_w
        if total > 0:
            parts.append(f"rot:{counter % total}/{total}")
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return digest[:32]


def _rotation_competes(asset: Asset) -> bool:
    """True iff the panel would alternate faces — both PM and a current
    reservation are eligible, no OOS preempting."""
    if _current_oos(asset) is not None:
        return False
    return _current_reservation(asset) is not None and _next_due_item(asset) is not None


def render_image(
    asset: Asset, display=None, *, service_url: str | None = None
) -> tuple[bytes, str]:
    """Pick the face, render it, and report which face we drew.

    Caller is expected to advance ``display.rotation_counter`` after a
    successful response when ``display`` is not None and the chosen
    face was the rotation outcome — see views.EPaperDisplayImageView.
    """
    face, source = _pick_face(asset, display)
    if face == FACE_OOS:
        return render_oos_image(asset, source), face
    if face == FACE_RESERVATION:
        return render_reservation_image(asset, source), face
    return render_pm_image(asset, service_url=service_url), face
