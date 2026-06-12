#!/usr/bin/env python3
"""Regenerate the storage-vision E2E marker fixture.

The Playwright spec at e2e/storage-vision.spec.ts uploads a JPEG with
a QR slot marker rendered at the top and a bright (~RGB 240) rectangle
beneath it. ``heuristic-v1`` (backend/storage_vision/services/
classification.py) reads the area below the marker, computes a
grayscale mean, and classifies the slot as ``empty`` with confidence
well above the slot's 0.50 threshold — so the processor creates a
``reconcile_empty`` pending observation. Approving that observation
zeros the item's stock, which triggers the existing
``StockReconciliation`` auto-reorder pipeline.

The marker payload (``VIS-E2E-BOLT``) MUST match the
``marker_code`` the spec seeds on the ``VisionSlot``.

Run from this directory:

    python3 generate_marker_fixture.py
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

import qrcode
from PIL import Image

MARKER_PAYLOAD = "VIS-E2E-BOLT"
OUTPUT = Path(__file__).parent / "marker-VIS-E2E-BOLT.jpg"


def build_fixture() -> bytes:
    qr = qrcode.QRCode(box_size=6, border=4)
    qr.add_data(MARKER_PAYLOAD)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    qr_img = qr_img.resize((300, 300))

    canvas = Image.new("RGB", (600, 800), (240, 240, 240))
    canvas.paste(qr_img, (150, 50))
    buf = BytesIO()
    canvas.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def main() -> None:
    data = build_fixture()
    OUTPUT.write_bytes(data)
    print(f"wrote {OUTPUT} ({len(data)} bytes)")


if __name__ == "__main__":
    main()
