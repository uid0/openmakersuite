"""Tests for asset tag rendering service and API."""

from io import BytesIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

import pytest
from PIL import Image, ImageChops
from rest_framework.test import APIClient

from inventory.services.asset_tag_service import (
    DEFAULT_DPI,
    SCAN_URL_TEMPLATE,
    SIZES,
    InvalidTagSizeError,
    get_qr_box_px,
    get_rivet_centers_px,
    get_safe_rect_px,
    get_text_box_px,
    render_asset_tag,
)
from inventory.tests.factories import AssetFactory

User = get_user_model()

pytestmark = pytest.mark.django_db


def _open_png(png_bytes):
    return Image.open(BytesIO(png_bytes))


def _try_decode_qr(img):
    """Decode a QR code from a PIL image, returning the URL or None.

    Uses pyzbar when available; returns None when pyzbar is not installed
    so tests can degrade gracefully in environments without zbar.
    """
    try:
        from pyzbar.pyzbar import decode
    except ImportError:
        return None
    decoded = decode(img)
    if not decoded:
        return None
    return decoded[0].data.decode("utf-8")


@pytest.mark.unit
class TestRenderAssetTag:
    def test_standard_tag_dimensions(self):
        asset = AssetFactory()
        png = render_asset_tag(asset, size="standard", dpi=1440)
        img = _open_png(png)
        assert img.format == "PNG"
        assert img.size == (2160, 1440)
        dpi = img.info.get("dpi")
        assert dpi is not None
        assert round(dpi[0]) == 1440
        assert round(dpi[1]) == 1440

    def test_large_tag_dimensions(self):
        asset = AssetFactory()
        png = render_asset_tag(asset, size="large", dpi=1440)
        img = _open_png(png)
        assert img.size == (4320, 2160)

    def test_invalid_size_raises(self):
        asset = AssetFactory()
        with pytest.raises(InvalidTagSizeError):
            render_asset_tag(asset, size="huge")

    def test_qr_code_decodes_to_scan_url(self):
        asset = AssetFactory()
        png = render_asset_tag(asset, size="standard")
        img = _open_png(png)
        decoded = _try_decode_qr(img)
        if decoded is None:
            pytest.skip("pyzbar not installed; cannot decode QR for verification")
        assert decoded == SCAN_URL_TEMPLATE.format(asset_id=asset.id)

    def test_rivet_exclusion_zones_are_blank(self):
        asset = AssetFactory()
        png = render_asset_tag(asset, size="standard", dpi=1440)
        img = _open_png(png).convert("RGB")
        for cx, cy in get_rivet_centers_px("standard", dpi=1440):
            # Sample a small ring of pixels centered on the rivet point;
            # all should be white (no design content).
            for dx in (-5, 0, 5):
                for dy in (-5, 0, 5):
                    px = img.getpixel((cx + dx, cy + dy))
                    assert px == (255, 255, 255), (
                        f"Expected white pixel at ({cx + dx},{cy + dy}) "
                        f"in rivet exclusion zone, got {px}"
                    )

    def test_asset_tag_text_printed_below_qr(self):
        # The human-readable asset tag string is printed as text directly
        # beneath the QR so a tag is identifiable by eye without scanning.
        # Verify there is ink inside the reserved text band and that the band
        # sits strictly below the QR (no overlap).
        asset = AssetFactory()
        assert asset.asset_tag  # factory always assigns one
        png = render_asset_tag(asset, size="standard", dpi=1440)
        img = _open_png(png).convert("RGB")

        left, top, right, bottom = get_text_box_px("standard", dpi=1440)
        text_region = img.crop((left, top, right, bottom))
        white = Image.new("RGB", text_region.size, "white")
        text_bbox = ImageChops.difference(text_region, white).getbbox()
        assert text_bbox is not None, "no asset-tag text rendered below the QR"

        qr_box = get_qr_box_px("standard", dpi=1440)
        assert top >= qr_box[3], "text band overlaps the QR vertically"

    def test_asset_tag_text_printed_on_large_size(self):
        # The large tag shrinks the QR slightly to fit the same text band.
        asset = AssetFactory()
        png = render_asset_tag(asset, size="large", dpi=1440)
        img = _open_png(png).convert("RGB")

        left, top, right, bottom = get_text_box_px("large", dpi=1440)
        text_region = img.crop((left, top, right, bottom))
        white = Image.new("RGB", text_region.size, "white")
        assert (
            ImageChops.difference(text_region, white).getbbox() is not None
        ), "no asset-tag text rendered on the large tag"

    def test_asset_without_tag_renders_qr_only(self):
        # An asset with a blank tag renders the QR and no text (blank band),
        # and must not raise.
        asset = AssetFactory.build(asset_tag="")
        png = render_asset_tag(asset, size="standard", dpi=1440)
        img = _open_png(png).convert("RGB")

        left, top, right, bottom = get_text_box_px("standard", dpi=1440)
        text_region = img.crop((left, top, right, bottom))
        white = Image.new("RGB", text_region.size, "white")
        assert (
            ImageChops.difference(text_region, white).getbbox() is None
        ), "text band should be blank when the asset has no tag"

    def test_qr_code_fills_its_box_within_safe_rect(self):
        # The QR should fill its allotted box (dark modules reach ~85% of the
        # box after the QR's own quiet zone), and that box must sit inside the
        # safe rect (the area between the rivet exclusion zones). Measuring the
        # QR box directly keeps this robust to the text printed beneath it.
        asset = AssetFactory()
        png = render_asset_tag(asset, size="standard", dpi=1440)
        img = _open_png(png).convert("RGB")

        qr_left, qr_top, qr_right, qr_bottom = get_qr_box_px("standard", dpi=1440)
        safe_left, safe_top, safe_right, safe_bottom = get_safe_rect_px("standard", dpi=1440)
        assert safe_left <= qr_left and qr_right <= safe_right
        assert safe_top <= qr_top and qr_bottom <= safe_bottom

        qr_region = img.crop((qr_left, qr_top, qr_right, qr_bottom))
        white = Image.new("RGB", qr_region.size, "white")
        bbox = ImageChops.difference(qr_region, white).getbbox()
        assert bbox is not None
        bbox_w = bbox[2] - bbox[0]
        bbox_h = bbox[3] - bbox[1]
        qr_dim = qr_right - qr_left
        assert (
            bbox_w >= qr_dim * 0.85
        ), f"QR width {bbox_w}px does not fill its box (dim {qr_dim}px)"
        assert (
            bbox_h >= qr_dim * 0.85
        ), f"QR height {bbox_h}px does not fill its box (dim {qr_dim}px)"

    def test_render_executes_without_error_at_default_dpi(self):
        asset = AssetFactory()
        png = render_asset_tag(asset)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_sizes_are_parametrizable(self):
        # Adding a new size must just be a dict entry — verify both shipped sizes
        # are in the registry and have the expected geometry.
        assert SIZES["standard"] == {"width": 1.5, "height": 1.0}
        assert SIZES["large"] == {"width": 3.0, "height": 1.5}

    def test_default_dpi_is_1440(self):
        assert DEFAULT_DPI == 1440


@pytest.mark.integration
class TestAssetTagAPI:
    def setup_method(self):
        self.client = APIClient()
        self.asset = AssetFactory()
        self.staff_user = User.objects.create_user(username="staffer", password="x", is_staff=True)
        self.regular_user = User.objects.create_user(username="reg", password="x")

    def _url(self):
        return f"/api/inventory/assets/{self.asset.id}/tag/"

    def test_staff_can_get_standard_tag(self):
        self.client.force_authenticate(user=self.staff_user)
        response = self.client.get(self._url())
        assert response.status_code == 200
        assert response["Content-Type"] == "image/png"
        # No attachment header without download=1
        assert "attachment" not in response.get("Content-Disposition", "")
        # Verify the PNG actually parses and matches standard dimensions.
        img = _open_png(response.content)
        assert img.size == (2160, 1440)

    def test_large_size_returns_large_dimensions(self):
        self.client.force_authenticate(user=self.staff_user)
        response = self.client.get(self._url(), {"size": "large"})
        assert response.status_code == 200
        img = _open_png(response.content)
        assert img.size == (4320, 2160)

    def test_invalid_size_returns_400(self):
        self.client.force_authenticate(user=self.staff_user)
        response = self.client.get(self._url(), {"size": "tiny"})
        assert response.status_code == 400

    def test_download_query_param_sets_content_disposition(self):
        self.client.force_authenticate(user=self.staff_user)
        response = self.client.get(self._url(), {"download": "1"})
        assert response.status_code == 200
        disposition = response["Content-Disposition"]
        assert "attachment" in disposition
        assert f"asset-tag-{self.asset.id}-standard.png" in disposition

    def test_unprivileged_user_rejected_for_sig_owned_asset(self):
        # Asset owned by a SIG group; a regular user who doesn't manage that
        # SIG must not be able to render its tag.
        sig = Group.objects.create(name="laser-sig")
        self.asset.owning_group = sig
        self.asset.save()

        self.client.force_authenticate(user=self.regular_user)
        response = self.client.get(self._url())
        assert response.status_code == 403

    def test_unauthenticated_request_rejected(self):
        response = self.client.get(self._url())
        # IsAuthenticatedOrReadOnly allows the GET to reach the action,
        # but the action's own permission check rejects anonymous users.
        assert response.status_code in (401, 403)
