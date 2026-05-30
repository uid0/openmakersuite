"""Render an ePaper PM-display PNG to a file for design previewing.

The XIAO 7.5" panel image is rendered server-side
(``forgekey.services.epaper_render.render_pm_image``). This command
exercises that renderer so the exact image a panel will show can be
inspected without a device in hand.

Run it inside the backend container, e.g.::

    # Synthetic asset (no DB rows persisted) — quickest design loop:
    python manage.py epaper_preview

    # A real asset / bound display:
    python manage.py epaper_preview --asset-id 5
    python manage.py epaper_preview --display-id <uuid>

    # Pick the sample status so you can eyeball each state:
    python manage.py epaper_preview --state overdue
    python manage.py epaper_preview --state never

    # Choose where the PNG lands (default /tmp/epaper_preview.png):
    python manage.py epaper_preview --out /tmp/foo.png

Copy it to the host with::

    docker cp oms-backend-1:/tmp/epaper_preview.png /tmp/epaper_preview.png
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone


class Command(BaseCommand):
    help = "Render an ePaper PM-display PNG to a file for previewing the layout."

    def add_arguments(self, parser):
        parser.add_argument("--asset-id", type=int, default=None)
        parser.add_argument("--display-id", type=str, default=None)
        parser.add_argument(
            "--state",
            choices=["ok", "warning", "overdue", "never", "none"],
            default="ok",
            help="Sample PM state to render (only used when no asset/display given).",
        )
        # Dev preview tool; /tmp is the documented drop point (docker cp).
        parser.add_argument("--out", type=str, default="/tmp/epaper_preview.png")  # nosec B108
        parser.add_argument(
            "--base-url",
            type=str,
            default="https://dms.openmakersuite.net",
            help="Public base URL used to build the scan-to-log QR target.",
        )

    def _service_url(self, base: str, display_id: str) -> str:
        return f"{base.rstrip('/')}/forgekey/epaper/{display_id}/service"

    def handle(self, *args, **opts):
        from forgekey.services.epaper_render import render_pm_image
        from inventory.models import Asset

        out = opts["out"]
        base = opts["base_url"]

        if opts["asset_id"] is not None:
            asset = Asset.objects.filter(pk=opts["asset_id"]).first()
            if asset is None:
                raise CommandError(f"No asset with id {opts['asset_id']}")
            display = asset.epaper_displays.first()
            did = str(display.pk) if display else f"sample-asset-{asset.pk}"
            self._write(
                out, render_pm_image(asset, service_url=self._service_url(base, did)), asset
            )
            return

        if opts["display_id"]:
            from forgekey.models import EPaperDisplay

            display = EPaperDisplay.objects.filter(pk=opts["display_id"]).first()
            if display is None:
                raise CommandError(f"No EPaperDisplay with id {opts['display_id']}")
            if display.asset_id is None:
                raise CommandError("Display is unbound (no asset); nothing to render.")
            url = self._service_url(base, str(display.pk))
            self._write(out, render_pm_image(display.asset, service_url=url), display.asset)
            return

        self._render_sample(out, opts["state"], base)

    def _render_sample(self, out: str, state: str, base: str) -> None:
        """Build a throwaway asset + maintenance item, render, roll the DB back."""
        from forgekey.services.epaper_render import render_pm_image
        from inventory.models import Asset, Location, MaintenanceItem

        interval = 30
        # Map the requested state to a "days since last completion" so the
        # renderer's own status logic produces it (no faking strings).
        last_done_days = {
            "ok": 5,  # well within interval → "X DAYS REMAINING"
            "warning": 26,  # inside the final 20% → "due soon"
            "overdue": 41,  # past the interval → "OVERDUE BY N DAYS"
            "never": None,  # recurring but never completed → "DUE NOW"
            "none": "no-item",  # no maintenance item at all
        }[state]

        with transaction.atomic():
            sfx = uuid4().hex[:6].upper()
            location = Location.objects.create(name=f"Preview Loc {sfx}")
            asset = Asset.objects.create(
                name='Bandsaw 14"',
                location=location,
                asset_tag=f"PREVIEW-{sfx}",
            )
            if last_done_days != "no-item":
                last_completed = (
                    timezone.now() - timedelta(days=last_done_days)
                    if last_done_days is not None
                    else None
                )
                MaintenanceItem.objects.create(
                    asset=asset,
                    title="Blade tension & alignment",
                    interval_days=interval,
                    last_completed_at=last_completed,
                )
            url = self._service_url(base, "00000000-0000-0000-0000-0000000000ab")
            self._write(out, render_pm_image(asset, service_url=url), asset)
            transaction.set_rollback(True)

    def _write(self, out: str, png: bytes, asset) -> None:
        with open(out, "wb") as fh:
            fh.write(png)
        self.stdout.write(
            self.style.SUCCESS(f"Wrote {len(png)} bytes to {out}  (asset: {asset.name})")
        )
