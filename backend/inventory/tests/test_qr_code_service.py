"""
Tests for QR code service.
"""

from io import BytesIO

import pytest
import qrcode
from PIL import Image
from qrcode.image.pil import PilImage

from customization.models import SiteSettings
from inventory.services.qr_code_service import QRCodeService
from inventory.tests.factories import AssetFactory, InventoryItemFactory, LocationFactory

pytestmark = pytest.mark.django_db


@pytest.mark.unit
class TestQRCodeService:
    """Tests for QR code service."""

    def test_generate_qr_code_image_basic(self):
        """Test generating a basic QR code image."""
        service = QRCodeService(include_logo=False)
        url = "https://example.com/test"
        qr_img = service.generate_qr_code_image(url)

        # Verify it's a valid PIL Image (PilImage is a subclass/wrapper)
        assert isinstance(qr_img, (Image.Image, PilImage))
        assert qr_img.size[0] > 0
        assert qr_img.size[1] > 0

        # Verify it can be saved to BytesIO
        buffer = BytesIO()
        qr_img.save(buffer, format="PNG")
        buffer.seek(0)
        img = Image.open(buffer)
        assert img.format == "PNG"

    def test_generate_qr_code_with_logo(self, sample_image):
        """Test generating QR code with logo when logo is available."""
        # Set up site settings with logo
        site_settings = SiteSettings.get()
        site_settings.logo = sample_image
        site_settings.save()

        service = QRCodeService(include_logo=True)
        url = "https://example.com/test"
        qr_img = service.generate_qr_code_image(url)

        # Verify it's a valid PIL Image (PilImage is a subclass/wrapper)
        assert isinstance(qr_img, (Image.Image, PilImage))
        assert qr_img.size[0] > 0
        assert qr_img.size[1] > 0

    def test_generate_qr_code_without_logo_when_none_available(self):
        """Test generating QR code when no logo is available."""
        # Ensure no logo is set
        site_settings = SiteSettings.get()
        site_settings.logo = None
        site_settings.save()

        service = QRCodeService(include_logo=True)
        url = "https://example.com/test"
        qr_img = service.generate_qr_code_image(url)

        # Verify it's a valid PIL Image (PilImage is a subclass/wrapper)
        assert isinstance(qr_img, (Image.Image, PilImage))
        # Format may not be set on PilImage, but it should have size
        assert qr_img.size[0] > 0
        assert qr_img.size[1] > 0

    def test_generate_qr_code_without_logo_when_disabled(self, sample_image):
        """Test generating QR code when logo embedding is disabled."""
        # Set up site settings with logo
        site_settings = SiteSettings.get()
        site_settings.logo = sample_image
        site_settings.save()

        service = QRCodeService(include_logo=False)
        url = "https://example.com/test"
        qr_img = service.generate_qr_code_image(url)

        # Verify it's a valid PIL Image (PilImage is a subclass/wrapper)
        assert isinstance(qr_img, (Image.Image, PilImage))
        assert qr_img.size[0] > 0
        assert qr_img.size[1] > 0

    def test_generate_for_item(self):
        """Test generating QR code for an inventory item."""
        item = InventoryItemFactory()
        assert not item.qr_code  # No QR code initially

        service = QRCodeService(include_logo=False)
        updated_item = service.generate_for_item(item)

        # Verify QR code was saved and model is updated
        assert updated_item.qr_code
        assert updated_item.qr_code.name.startswith("inventory/qrcodes/qr_")
        # Verify the original item instance is also updated
        assert item.qr_code
        assert item.qr_code.name.startswith("inventory/qrcodes/qr_")
        # Verify database has the updated path
        item.refresh_from_db()
        assert item.qr_code.name.startswith("inventory/qrcodes/qr_")

    def test_generate_for_asset(self):
        """Test generating QR code for an asset."""
        asset = AssetFactory()
        assert not asset.qr_code  # No QR code initially

        service = QRCodeService(include_logo=False)
        updated_asset = service.generate_for_asset(asset)

        # Verify QR code was saved and model is updated
        assert updated_asset.qr_code
        assert updated_asset.qr_code.name.startswith("assets/qrcodes/asset_qr_")
        # Verify the original asset instance is also updated
        assert asset.qr_code
        assert asset.qr_code.name.startswith("assets/qrcodes/asset_qr_")
        # Verify database has the updated path
        asset.refresh_from_db()
        assert asset.qr_code.name.startswith("assets/qrcodes/asset_qr_")

    def test_generate_for_location(self):
        """Test generating QR code for a location."""
        location = LocationFactory()
        assert not location.qr_code  # No QR code initially

        service = QRCodeService(include_logo=False)
        updated_location = service.generate_for_location(location)

        # Verify QR code was saved and model is updated
        assert updated_location.qr_code
        assert updated_location.qr_code.name.startswith("inventory/location_qrcodes/location_qr_")
        # Verify the original location instance is also updated
        assert location.qr_code
        assert location.qr_code.name.startswith("inventory/location_qrcodes/location_qr_")
        # Verify database has the updated path
        location.refresh_from_db()
        assert location.qr_code.name.startswith("inventory/location_qrcodes/location_qr_")

    def test_qr_code_validation_with_pyzbar(self):
        """Test QR code validation when pyzbar is available."""
        try:
            from pyzbar.pyzbar import decode

            service = QRCodeService(include_logo=False)
            url = "https://example.com/test-url"
            qr_img = service.generate_qr_code_image(url)

            # Round-trip through PNG bytes to get a real PIL.Image — the
            # service returns a qrcode.image.pil.PilImage wrapper that
            # pyzbar's isinstance() check misses under Pillow 12+. See
            # qr_code_service._validate_qr_code for the same pattern.
            buffer = BytesIO()
            qr_img.save(buffer, format="PNG")
            buffer.seek(0)
            pil_image = Image.open(buffer)

            # Verify the QR code can be decoded
            decoded_objects = decode(pil_image)
            assert len(decoded_objects) > 0
            decoded_url = decoded_objects[0].data.decode("utf-8")
            assert decoded_url == url

        except ImportError:
            pytest.skip("pyzbar not available - skipping validation test")

    def test_qr_code_validation_without_pyzbar(self, monkeypatch):
        """Test QR code generation works even without pyzbar."""

        # Mock ImportError for pyzbar
        def mock_import_error(*args, **kwargs):
            raise ImportError("No module named 'pyzbar'")

        service = QRCodeService(include_logo=False)
        url = "https://example.com/test-url"

        # Should not raise an error even without pyzbar
        qr_img = service.generate_qr_code_image(url)
        assert isinstance(qr_img, (Image.Image, PilImage))

    def test_embed_logo_handles_invalid_logo(self):
        """Test that invalid logo files are handled gracefully."""
        service = QRCodeService(include_logo=True)

        # Create a QR code first
        url = "https://example.com/test"
        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L)
        qr.add_data(url)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white")

        # Try to embed a non-existent logo
        # This should not raise an error, just return the original image
        result = service._embed_logo(qr_img, None)
        assert result is not None

    def test_custom_error_correction_level(self):
        """Test generating QR code with custom error correction level."""
        service = QRCodeService(include_logo=False)
        url = "https://example.com/test"

        # Test with different error correction levels
        for error_correction in [
            qrcode.constants.ERROR_CORRECT_L,
            qrcode.constants.ERROR_CORRECT_M,
            qrcode.constants.ERROR_CORRECT_Q,
            qrcode.constants.ERROR_CORRECT_H,
        ]:
            qr_img = service.generate_qr_code_image(url, error_correction=error_correction)
            assert isinstance(qr_img, (Image.Image, PilImage))
            assert qr_img.size[0] > 0
            assert qr_img.size[1] > 0
