"""Server-side PNG renderer for XIAO 7.5" ePaper PM displays.

The XIAO 7.5" panel resolution is 800x480 monochrome (Seeed Studio
Wio E-Paper 7.5"). The firmware fetches the rendered PNG over HTTPS
on wake-up, flashes the panel, and sleeps — no client-side layout.
Server-side render means the panel itself stays simple, fonts come
from one place, and ops can preview the exact image a given asset
will display without firmware in hand.

Image content per panel (top-to-bottom):

    [eyebrow]  ASSET NAME              ← serif display, large
    [task]     PM task                 ← sans, medium
    [status]   X days remaining        ← mono, large, status-coloured
                                         (or "OVERDUE" / "NEVER")
    [footer]   Last serviced: DATE     ← sans, small

The render is deterministic for a given snapshot of an asset's PM
state, so we expose ``compute_snapshot_etag`` for the HTTP layer to
short-circuit unchanged requests with 304 Not Modified.
"""

from __future__ import annotations

import hashlib
import io
from typing import Iterable

from django.utils import timezone

from PIL import Image, ImageDraw, ImageFont

from inventory.models import Asset
from preventive_maintenance.models import PMSchedule

# Stick to PIL's default font so the render never depends on a font
# file that might be absent from the worker container. The default is
# fixed-width and renders crisply on a 1-bpp panel, which is what we
# want anyway.
_FONT = ImageFont.load_default()

EPAPER_WIDTH = 800
EPAPER_HEIGHT = 480
_BG = 255  # white
_FG = 0  # black


def _next_due_schedule(asset: Asset) -> PMSchedule | None:
    """Pick the schedule that should drive the panel display.

    Priority order:
      1. Any OVERDUE schedule (most urgent first by negative
         days_until_due).
      2. The WARNING schedule with the fewest days remaining.
      3. The OK schedule with the fewest days remaining.
      4. A NEVER-serviced schedule if no service log exists.

    Returns ``None`` when the asset has no active schedules.
    """
    schedules = list(asset.pm_schedules.filter(is_active=True))
    if not schedules:
        return None

    def sort_key(schedule: PMSchedule) -> tuple[int, int]:
        # Status priority: overdue=0, warning=1, ok=2, never=3.
        status_priority = {
            PMSchedule.STATUS_OVERDUE: 0,
            PMSchedule.STATUS_WARNING: 1,
            PMSchedule.STATUS_OK: 2,
            PMSchedule.STATUS_NEVER: 3,
        }
        days_remaining = schedule.days_until_due()
        # For OVERDUE rows, days_until_due is negative — the *more*
        # negative, the more overdue. For OK/WARNING, the *fewer*
        # remaining days, the higher priority. NEVER → None, push to
        # the end with a large sentinel.
        sort_remaining = days_remaining if days_remaining is not None else 10_000
        return (status_priority[schedule.status()], sort_remaining)

    return sorted(schedules, key=sort_key)[0]


def _snapshot_fingerprint(asset: Asset, schedules: Iterable[PMSchedule]) -> str:
    """Return a stable hex fingerprint of the inputs to the rendered image.

    The HTTP layer uses this as the response ETag; an unchanged
    fingerprint means the panel can keep displaying its cached PNG.
    """
    parts: list[str] = [str(asset.pk), asset.name]
    for schedule in schedules:
        last = schedule.last_log()
        last_at = last.performed_at.isoformat() if last else "never"
        parts.append(
            "|".join(
                [
                    str(schedule.pk),
                    schedule.task_name,
                    str(schedule.interval_days),
                    schedule.status(),
                    last_at,
                ]
            )
        )
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return digest[:32]


def compute_snapshot_etag(asset: Asset) -> str:
    """Public helper for the view layer: stable ETag for an asset's PM state."""
    schedules = list(asset.pm_schedules.filter(is_active=True).order_by("pk"))
    return _snapshot_fingerprint(asset, schedules)


def _draw_centered(
    draw: ImageDraw.ImageDraw,
    text: str,
    y: int,
    font: ImageFont.ImageFont,
    fill: int = _FG,
) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    x = (EPAPER_WIDTH - text_w) // 2
    draw.text((x, y), text, font=font, fill=fill)


def _status_line(schedule: PMSchedule | None) -> str:
    if schedule is None:
        return "NO PM SCHEDULE"
    status = schedule.status()
    if status == PMSchedule.STATUS_NEVER:
        return "NEVER SERVICED"
    days = schedule.days_until_due() or 0
    if status == PMSchedule.STATUS_OVERDUE:
        # days is negative when overdue; show the absolute lateness.
        return f"OVERDUE BY {abs(days)} DAYS"
    return f"{days} DAYS REMAINING"


def render_pm_image(asset: Asset) -> bytes:
    """Render the full panel image for ``asset`` and return PNG bytes."""
    img = Image.new("L", (EPAPER_WIDTH, EPAPER_HEIGHT), color=_BG)
    draw = ImageDraw.Draw(img)

    # Outer rule — thin border so the panel feels intentional, not
    # like the firmware forgot to paint corners.
    draw.rectangle([(8, 8), (EPAPER_WIDTH - 8, EPAPER_HEIGHT - 8)], outline=_FG, width=2)

    # Eyebrow.
    _draw_centered(draw, "PREVENTIVE MAINTENANCE", 36, _FONT)

    # Asset name — large, centred.
    _draw_centered(draw, asset.name.upper(), 90, _FONT)

    schedule = _next_due_schedule(asset)
    task_line = schedule.task_name if schedule else "—"
    _draw_centered(draw, task_line, 180, _FONT)

    # Status block — the headline number. Drawn into an inverted bar
    # so the panel still reads clearly on a quick glance.
    status_y = 250
    status_text = _status_line(schedule)
    inverted = schedule is not None and schedule.status() in (
        PMSchedule.STATUS_OVERDUE,
        PMSchedule.STATUS_NEVER,
    )
    if inverted:
        draw.rectangle([(40, status_y - 20), (EPAPER_WIDTH - 40, status_y + 60)], fill=_FG)
        _draw_centered(draw, status_text, status_y, _FONT, fill=_BG)
    else:
        _draw_centered(draw, status_text, status_y, _FONT)

    # Footer.
    last_line = "Last serviced: never"
    if schedule:
        log = schedule.last_log()
        if log is not None:
            last_line = f"Last serviced: {log.performed_at.date().isoformat()}"
    rendered_line = f"Rendered: {timezone.now().date().isoformat()}"
    _draw_centered(draw, last_line, 380, _FONT)
    _draw_centered(draw, rendered_line, 410, _FONT)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
