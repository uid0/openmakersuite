"""Tests for the maker box label renderer (AC4)."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from maker_boxes.models import MakerBox
from maker_boxes.services.label_service import (
    DEFAULT_DPI,
    LABEL_HEIGHT_INCHES,
    LABEL_WIDTH_INCHES,
    render_box_label,
)


def _open_png(png_bytes: bytes) -> Image.Image:
    return Image.open(BytesIO(png_bytes))


@pytest.mark.django_db
def test_label_dimensions_match_business_card_at_600dpi():
    box = MakerBox.objects.create(
        bin_id="PSB-001",
        assigned_username="alovelace",
        first_name="Ada",
        last_name="Lovelace",
    )
    png = render_box_label(box)
    img = _open_png(png)
    assert img.format == "PNG"
    expected = (
        int(round(LABEL_WIDTH_INCHES * DEFAULT_DPI)),
        int(round(LABEL_HEIGHT_INCHES * DEFAULT_DPI)),
    )
    assert img.size == expected
    dpi = img.info.get("dpi")
    assert dpi is not None
    assert round(dpi[0]) == DEFAULT_DPI
    assert round(dpi[1]) == DEFAULT_DPI


def test_qr_payload_uses_frontend_scan_route_for_assigned_rows(settings):
    """PR 3: converted rows encode the self-resolving scanner URL."""
    from maker_boxes.services.label_service import _qr_payload

    settings.FRONTEND_URL = "https://oms.example.com"
    assert (
        _qr_payload("MBX-007", "alovelace")
        == "https://oms.example.com/scan/makerbox/MBX-007/alovelace/"
    )


def test_qr_payload_falls_back_to_username_when_unassigned():
    from maker_boxes.services.label_service import _qr_payload

    # No bin_id -> plain username, since the URL would be malformed.
    assert _qr_payload("", "alovelace") == "alovelace"
    # No username either -> "unassigned" sentinel.
    assert _qr_payload("", "") == "unassigned"


def test_qr_payload_url_encodes_special_characters(settings):
    from maker_boxes.services.label_service import _qr_payload

    settings.FRONTEND_URL = "https://oms.example.com/"
    # Trailing slash on FRONTEND_URL is stripped; spaces in username
    # are percent-encoded so the QR resolves cleanly on a phone.
    payload = _qr_payload("MBX-007", "ada lovelace")
    assert payload == "https://oms.example.com/scan/makerbox/MBX-007/ada%20lovelace/"
