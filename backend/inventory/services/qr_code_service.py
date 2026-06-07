"""
QR code generation service for assets, items, and locations.

This service provides QR code generation with optional logo embedding
and validation to ensure the QR code encodes the correct URL.
"""

from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.files import File

import qrcode
from PIL import Image
from qrcode.image.pil import PilImage

from customization.models import SiteSettings
from inventory.utils.code_generator import generate_unique_code

if TYPE_CHECKING:
    from inventory.models import Asset, InventoryItem, Location
    from project_storage.models import ProjectStorageStint


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
    ) -> PilImage:
        """
        Generate a QR code image with optional logo embedding.

        Args:
            url: The URL to encode in the QR code
            error_correction: Error correction level (L, M, Q, or H)
            box_size: Size of each box in pixels
            border: Border size in boxes

        Returns:
            PIL Image object containing the QR code

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

        return img

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
            # Render the QR to a PNG buffer and reopen as a real PIL.Image.
            # `img` is a qrcode.image.pil.PilImage wrapper, not a
            # PIL.Image.Image — pyzbar's isinstance() check misses the
            # wrapper and falls through to a tuple-unpack path that fails
            # with "cannot unpack non-iterable PilImage object" under
            # Pillow 12+. Going via PNG bytes sidesteps the wrapper and
            # also normalizes the mode so pyzbar's grayscale conversion
            # always lands on a supported input.
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            buffer.seek(0)
            pil_image = Image.open(buffer)

            # Try to decode using pyzbar if available, otherwise skip validation.
            try:
                from pyzbar.pyzbar import decode

                decoded_objects = decode(pil_image)
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

        # Generate QR code image
        qr_img = self.generate_qr_code_image(scan_url)

        # Convert to BytesIO for saving
        buffer = BytesIO()
        qr_img.save(buffer, format="PNG")
        buffer.seek(0)

        filename = f"asset_qr_{asset.id}.png"
        # Delete old QR code if it exists (to force regeneration)
        if asset.qr_code:
            asset.qr_code.delete(save=False)
        # Save the QR code file and update the model
        asset.qr_code.save(filename, File(buffer), save=True)
        # Refresh from database to ensure model has the latest QR code path
        asset.refresh_from_db()

        return asset

    def generate_for_stint(self, stint) -> "ProjectStorageStint":
        """Generate and save a QR PNG for a project-storage stint.

        Mirrors generate_for_asset exactly: encodes the same /scan/...
        URL the inventory convention uses (so a phone-camera scan opens
        the warden detail page wired in PR 3), goes through the same
        logo-embed + pyzbar validation, persists the file on the
        stint.qr_code ImageField, and refreshes from the DB so the
        serializer sees the new url.
        """
        frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
        scan_url = f"{frontend_url}/scan/project-storage/{stint.stint_id}"

        qr_img = self.generate_qr_code_image(scan_url)

        buffer = BytesIO()
        qr_img.save(buffer, format="PNG")
        buffer.seek(0)

        filename = f"project_storage_qr_{stint.stint_id}.png"
        if stint.qr_code:
            stint.qr_code.delete(save=False)
        stint.qr_code.save(filename, File(buffer), save=True)
        stint.refresh_from_db()
        return stint

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

        # Generate QR code image
        qr_img = self.generate_qr_code_image(scan_url)

        # Convert to BytesIO for saving
        buffer = BytesIO()
        qr_img.save(buffer, format="PNG")
        buffer.seek(0)

        filename = f"qr_{item.id}.png"
        # Delete old QR code if it exists (to force regeneration)
        if item.qr_code:
            item.qr_code.delete(save=False)
        # Save the QR code file and update the model
        item.qr_code.save(filename, File(buffer), save=True)
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

        # Generate or get access code
        if not location.access_code:
            from inventory.models import Location

            location.access_code = generate_unique_code(Location, "access_code")
            location.save(update_fields=["access_code"])

        # Generate QR code image
        qr_img = self.generate_qr_code_image(scan_url)

        # Convert to BytesIO for saving
        buffer = BytesIO()
        qr_img.save(buffer, format="PNG")
        buffer.seek(0)

        filename = f"location_qr_{location.id}.png"
        # Delete old QR code if it exists (to force regeneration)
        if location.qr_code:
            location.qr_code.delete(save=False)
        # Save the QR code file and update the model
        location.qr_code.save(filename, File(buffer), save=True)
        # Refresh from database to ensure model has the latest QR code path
        location.refresh_from_db()

        return location
