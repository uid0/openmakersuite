#!/usr/bin/env python3
"""Project Storage print daemon — run me on a Raspberry Pi.

Polls the OMS Project Storage print-queue endpoint for stints that
haven't been printed yet, downloads each label PNG, sends it to the
configured printer, and reports the print back to OMS so the queue
drains.

Two printer backends are supported (auto-selected per stint by the
backend's `print_target` field, or overridden globally via env):

  - Brother QL label printer (recommended):
        pip install brother_ql
        Plug in via USB, find the device file (e.g. /dev/usb/lp0), set
            OMS_PRINTER_KIND=brother_ql
            OMS_BROTHER_DEVICE=/dev/usb/lp0
            OMS_BROTHER_MODEL=QL-820NWB     # match your hardware
            OMS_BROTHER_LABEL=62            # 62mm continuous

  - Epson TM receipt printer:
        Plug in via USB, install CUPS, add the printer.
        Set OMS_PRINTER_KIND=epson_tm and (optionally)
            OMS_EPSON_CUPS_QUEUE=TM_T20III   # CUPS queue name

Environment configuration (all required ones in CAPS, all optional in
parens):

    OMS_API_BASE          e.g. https://oms.example.com/api
    (OMS_API_TOKEN)       Bearer token if the deployment fronts these
                          endpoints with auth; default endpoints are
                          AllowAny so this is usually empty.
    OMS_POLL_INTERVAL_S   default 10
    OMS_PRINTER_KIND      brother_ql | epson_tm   (overrides the
                          per-stint print_target if set)

Run:
    python3 print_daemon.py

Install as a systemd service: see project_storage.service in this
directory.

Design notes
------------
- The daemon is intentionally single-threaded and stateless. Each loop
  iteration: GET queue → for each entry, fetch label PNG → print →
  POST mark-printed. If any step fails for a given stint we log and
  move on; the queue will surface that stint again next loop.
- We never assume a stint will be printed "soon" — `created_at` order
  is the queue order, but the OMS side won't time out if the daemon
  takes a while; the warden can also reprint by toggling printed_at
  via the admin if a label gets crushed.
- HTTP errors backoff exponentially up to 60s. Printer errors don't
  backoff — they're usually paper out / cover open and they want a
  human's attention, not a sleep.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from io import BytesIO
from typing import Iterable

import requests

LOG = logging.getLogger("project_storage_print_daemon")

DEFAULT_POLL_INTERVAL = 10
MAX_HTTP_BACKOFF = 60


# ---------------------------------------------------------------------------
# Printer drivers
# ---------------------------------------------------------------------------


def _print_brother(png_bytes: bytes) -> None:
    """Send PNG bytes to a Brother QL-series label printer via brother_ql."""
    from brother_ql.backends.helpers import send  # type: ignore
    from brother_ql.conversion import convert  # type: ignore
    from brother_ql.raster import BrotherQLRaster  # type: ignore
    from PIL import Image  # type: ignore

    device = os.environ.get("OMS_BROTHER_DEVICE", "/dev/usb/lp0")
    model = os.environ.get("OMS_BROTHER_MODEL", "QL-820NWB")
    label = os.environ.get("OMS_BROTHER_LABEL", "62")

    img = Image.open(BytesIO(png_bytes))
    qlr = BrotherQLRaster(model)
    qlr.exception_on_warning = True
    instructions = convert(
        qlr=qlr,
        images=[img],
        label=label,
        rotate="0",
        threshold=70.0,
        dither=False,
        compress=False,
        red=False,
        dpi_600=False,
        hq=True,
        cut=True,
    )
    send(
        instructions=instructions,
        printer_identifier=device,
        backend_identifier="linux_kernel",
        blocking=True,
    )


def _print_epson(png_bytes: bytes) -> None:
    """Send PNG bytes to an Epson TM receipt printer via CUPS `lp`."""
    import subprocess  # noqa: S404 # nosec B404 - controlled lp invocation on the Pi
    import tempfile

    queue = os.environ.get("OMS_EPSON_CUPS_QUEUE")
    args = ["lp"]
    if queue:
        args += ["-d", queue]

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
        fh.write(png_bytes)
        path = fh.name
    args.append(path)
    try:
        # nosec B603 - args is a fixed list (lp + -d <env queue> + temp path);
        # no user input ever reaches argv. Shell=False (default) so even the
        # env-derived queue name can't expand.
        subprocess.run(args, check=True, capture_output=True, text=True)  # noqa: S603  # nosec B603
    finally:
        os.unlink(path)


PRINTERS = {
    "brother_ql": _print_brother,
    "epson_tm": _print_epson,
}


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------


class OMSClient:
    def __init__(self, base: str, token: str | None = None) -> None:
        self.base = base.rstrip("/")
        self.session = requests.Session()
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    def queue(self) -> list[dict]:
        resp = self.session.get(f"{self.base}/project-storage/stints/print-queue/", timeout=15)
        resp.raise_for_status()
        return resp.json()

    def label(self, label_url: str) -> bytes:
        # The queue gives us an absolute label_url; honour it instead of
        # reconstructing the path on this side.
        resp = self.session.get(label_url, timeout=30)
        resp.raise_for_status()
        return resp.content

    def mark_printed(self, stint_id: str, note: str = "") -> None:
        resp = self.session.post(
            f"{self.base}/project-storage/stints/{stint_id}/mark-printed/",
            json={"note": note},
            timeout=15,
        )
        resp.raise_for_status()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def _pick_printer(stint: dict) -> str:
    forced = os.environ.get("OMS_PRINTER_KIND", "").strip()
    if forced:
        return forced
    return stint.get("print_target") or "brother_ql"


def _process_one(client: OMSClient, stint: dict) -> None:
    kind = _pick_printer(stint)
    driver = PRINTERS.get(kind)
    if driver is None:
        LOG.error(
            "stint %s: unknown printer kind %r — leaving in queue",
            stint["stint_id"],
            kind,
        )
        return

    LOG.info("stint %s: fetching label", stint["stint_id"])
    png = client.label(stint["label_url"])

    LOG.info("stint %s: printing via %s", stint["stint_id"], kind)
    driver(png)

    LOG.info("stint %s: confirming print to OMS", stint["stint_id"])
    client.mark_printed(stint["stint_id"], note=f"printed via {kind}")


def _loop(client: OMSClient, poll_s: float) -> None:
    http_backoff = 1.0
    while True:
        try:
            stints: Iterable[dict] = client.queue()
            http_backoff = 1.0
        except requests.RequestException as exc:
            LOG.warning("queue poll failed (%s); backing off %.0fs", exc, http_backoff)
            time.sleep(http_backoff)
            http_backoff = min(http_backoff * 2, MAX_HTTP_BACKOFF)
            continue

        for stint in stints:
            try:
                _process_one(client, stint)
            except requests.RequestException as exc:
                LOG.warning(
                    "stint %s: HTTP failure (%s) — will retry next poll",
                    stint["stint_id"],
                    exc,
                )
            except Exception as exc:  # noqa: BLE001 — printer drivers raise many shapes
                LOG.exception(
                    "stint %s: print failed (%s) — will retry next poll",
                    stint["stint_id"],
                    exc,
                )

        time.sleep(poll_s)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    base = os.environ.get("OMS_API_BASE")
    if not base:
        LOG.error("OMS_API_BASE is required (e.g. https://oms.example.com/api)")
        return 2

    token = os.environ.get("OMS_API_TOKEN") or None
    try:
        poll_s = float(os.environ.get("OMS_POLL_INTERVAL_S", DEFAULT_POLL_INTERVAL))
    except ValueError:
        poll_s = DEFAULT_POLL_INTERVAL

    client = OMSClient(base, token=token)
    LOG.info("polling %s every %.0fs", base, poll_s)
    try:
        _loop(client, poll_s)
    except KeyboardInterrupt:
        LOG.info("interrupted; exiting")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
