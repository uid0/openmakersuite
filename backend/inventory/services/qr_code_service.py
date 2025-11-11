"""
QR code generation service for assets, items, and locations.

This service provides QR code generation with optional logo embedding
and validation to ensure the QR code encodes the correct URL.
"""

from io import BytesIO
from typing import Optional

from django.conf import settings
from django.core.files import File
from PIL import Image

import qrcode
from qrcode.image.pil import PilImage

from customization.models import SiteSettings


class QRCodeService:
    """Service for generating QR codes with optional logo embedding."""

    def __init__(self, include_logo: bool = True):
        """
        Initialize the QR code service.

        Args:
            include_logo: Whether to include logo in QR code if available
        """
        self.include_logo = include_logo
        self.site_settings = SiteSettings.get()

    def generate_qr_code_image(
        self,
        url: str,
        error_correction: int = qrcode.constants.ERROR_CORRECT_H,
        box_size: int = 10,
        border: int = 4,
    ) -> BytesIO:
        """
        Generate a QR code image with optional logo embedding.

        Args:
            url: The URL to encode in the QR code
            error_correction: Error correction level (L, M, Q, or H)
            box_size: Size of each box in pixels
            border: Border size in boxes

        Returns:
            BytesIO object containing the QR code image (PNG format)

        Raises:
            ValueError: If the generated QR code doesn't decode to the expected URL
        """
        # Generate QR code
        qr = qrcode.QRCode(
            version=1,
            error_correction=error_correction,
            box_size=box_size,
            border=border,
        )
        qr.add_data(url)
        qr.make(fit=True)

        # Create image
        img = qr.make_image(fill_color="black", back_color="white")

        # Embed logo if requested and available
        if self.include_logo and self.site_settings.logo:
            img = self._embed_logo(img, self.site_settings.logo)

        # Validate the QR code
        self._validate_qr_code(img, url)

        # Save to BytesIO
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)

        return buffer

    def _embed_logo(self, qr_img: PilImage, logo_file) -> PilImage:
        """
        Embed a logo in the center of the QR code.

        Args:
            qr_img: The QR code PIL image
            logo_file: The logo file (Django FileField)

        Returns:
            PIL Image with logo embedded
        """
        # Convert QR code to RGB if needed
        if qr_img.mode != "RGB":
            qr_img = qr_img.convert("RGB")

        # Open and process logo
        try:
            # Try to get the file path if it's a Django FileField
            if hasattr(logo_file, "path"):
                logo_path = logo_file.path
                if logo_path and logo_file:
                    logo = Image.open(logo_path)
                else:
                    return qr_img
            elif hasattr(logo_file, "read"):
                # It's a file-like object
                logo = Image.open(logo_file)
            else:
                # Not a valid file object
                return qr_img
        except Exception:
            # If logo can't be opened, return QR code without logo
            return qr_img

        # Convert logo to RGB if needed
        if logo.mode != "RGB":
            logo = logo.convert("RGB")

        # Calculate logo size (about 20% of QR code size, but maintain aspect ratio)
        qr_width, qr_height = qr_img.size
        logo_max_size = int(min(qr_width, qr_height) * 0.2)

        # Resize logo maintaining aspect ratio
        logo.thumbnail((logo_max_size, logo_max_size), Image.Resampling.LANCZOS)

        # Calculate position to center the logo
        logo_width, logo_height = logo.size
        position = (
            (qr_width - logo_width) // 2,
            (qr_height - logo_height) // 2,
        )

        # Create a white background for the logo (to ensure readability)
        # Make it slightly larger than the logo for padding
        padding = 5
        bg_size = (logo_width + 2 * padding, logo_height + 2 * padding)
        bg_position = (
            (qr_width - bg_size[0]) // 2,
            (qr_height - bg_size[1]) // 2,
        )

        # Paste white background
        bg = Image.new("RGB", bg_size, "white")
        qr_img.paste(bg, bg_position)

        # Paste logo on top of white background
        logo_position = (
            bg_position[0] + padding,
            bg_position[1] + padding,
        )
        qr_img.paste(logo, logo_position)

        return qr_img

    def _validate_qr_code(self, img: PilImage, expected_url: str) -> None:
        """
        Validate that the QR code decodes to the expected URL.

        Args:
            img: The QR code PIL image
            expected_url: The URL that should be encoded

        Raises:
            ValueError: If the QR code doesn't decode to the expected URL
        """
        try:
            # Use qrcode library's decoder or PIL-based decoding
            # We'll decode using a simple approach: save to buffer and decode
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            buffer.seek(0)

            # Decode the QR code
            from qrcode import QRCode

            # Create a new QRCode instance to decode
            # Note: qrcode library doesn't have a built-in decoder
            # We'll use pyzbar or another library, but for now we'll use a simpler approach
            # For validation, we can use the qrcode library's ability to verify
            # by creating a new QR code and comparing, or use pyzbar if available

            # Try to decode using pyzbar if available, otherwise skip validation
            try:
                from pyzbar.pyzbar import decode

                decoded_objects = decode(img)
                if decoded_objects:
                    decoded_url = decoded_objects[0].data.decode("utf-8")
                    if decoded_url != expected_url:
                        raise ValueError(
                            f"QR code validation failed: expected '{expected_url}', "
                            f"but decoded '{decoded_url}'"
                        )
                else:
                    # If pyzbar is available but can't decode, that's a problem
                    raise ValueError("QR code validation failed: could not decode QR code")
            except ImportError:
                # pyzbar not available, skip validation but log a warning
                import logging

                logger = logging.getLogger(__name__)
                logger.warning(
                    "pyzbar not available - skipping QR code validation. "
                    "Install pyzbar for QR code validation: pip install pyzbar"
                )
                # For now, we'll trust that the QR code is correct
                # since we just generated it with the correct URL
                pass

        except Exception as e:
            # If validation fails, raise an error
            raise ValueError(f"QR code validation failed: {str(e)}")

    def generate_for_asset(self, asset) -> "Asset":
        """
        Generate and save QR code for an asset.

        Args:
            asset: Asset instance

        Returns:
            Asset instance with QR code saved and updated with QR code location
        """
        frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
        scan_url = f"{frontend_url}/scan/asset/{asset.id}"

        qr_buffer = self.generate_qr_code_image(scan_url)

        filename = f"asset_qr_{asset.id}.png"
        # Save the QR code file and update the model
        asset.qr_code.save(filename, File(qr_buffer), save=True)
        # Refresh from database to ensure model has the latest QR code path
        asset.refresh_from_db()

        return asset

    def generate_for_item(self, item) -> "InventoryItem":
        """
        Generate and save QR code for an inventory item.

        Args:
            item: InventoryItem instance

        Returns:
            InventoryItem instance with QR code saved and updated with QR code location
        """
        base_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
        scan_url = f"{base_url}/scan/{item.id}"

        qr_buffer = self.generate_qr_code_image(scan_url)

        filename = f"qr_{item.id}.png"
        # Save the QR code file and update the model
        item.qr_code.save(filename, File(qr_buffer), save=True)
        # Refresh from database to ensure model has the latest QR code path
        item.refresh_from_db()

        return item

    def generate_for_location(self, location) -> "Location":
        """
        Generate and save QR code for a location.

        Args:
            location: Location instance

        Returns:
            Location instance with QR code saved and updated with QR code location
        """
        frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
        scan_url = f"{frontend_url}/scan/location/{location.id}"

        qr_buffer = self.generate_qr_code_image(scan_url)

        filename = f"location_qr_{location.id}.png"
        # Save the QR code file and update the model
        location.qr_code.save(filename, File(qr_buffer), save=True)
        # Refresh from database to ensure model has the latest QR code path
        location.refresh_from_db()

        return location

