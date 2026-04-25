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
