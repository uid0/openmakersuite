"""
QR code generation service for donation items.

Generates QR codes for donation items and creates printable sticker sheets.
"""

from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.files import File

import qrcode
from PIL import Image, ImageDraw, ImageFont

from customization.models import SiteSettings
from inventory.utils.code_generator import generate_unique_code

if TYPE_CHECKING:
    from ..models import DonationItem


class DonationItemQRCodeService:
    """Service for generating QR codes for donation items."""

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
    ) -> qrcode.image.pil.PilImage:
        """
        Generate a QR code image with optional logo embedding.

        Args:
            url: The URL to encode in the QR code
            error_correction: Error correction level (L, M, Q, or H)
            box_size: Size of each box in pixels
            border: Border size in boxes

        Returns:
            PIL Image object containing the QR code
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

        return img

    def _embed_logo(
        self, qr_img: qrcode.image.pil.PilImage, logo_file
    ) -> qrcode.image.pil.PilImage:
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
            if hasattr(logo_file, "path"):
                logo_path = logo_file.path
                if logo_path and logo_file:
                    logo = Image.open(logo_path)
                else:
                    return qr_img
            elif hasattr(logo_file, "read"):
                logo = Image.open(logo_file)
            else:
                return qr_img
        except Exception:
            return qr_img

        # Convert logo to RGB if needed
        if logo.mode != "RGB":
            logo = logo.convert("RGB")

        # Calculate logo size (about 20% of QR code size)
        qr_width, qr_height = qr_img.size
        logo_max_size = int(min(qr_width, qr_height) * 0.2)

        # Resize logo maintaining aspect ratio
        logo.thumbnail((logo_max_size, logo_max_size), Image.Resampling.LANCZOS)

        # Calculate position to center the logo
        logo_width, logo_height = logo.size
        padding = 5
        bg_size = (logo_width + 2 * padding, logo_height + 2 * padding)
        bg_position = (
            (qr_width - bg_size[0]) // 2,
            (qr_height - bg_size[1]) // 2,
        )

        # Paste white background
        bg = Image.new("RGB", bg_size, "white")
        qr_img.paste(bg, bg_position)

        # Paste logo on top
        logo_position = (
            bg_position[0] + padding,
            bg_position[1] + padding,
        )
        qr_img.paste(logo, logo_position)

        return qr_img

    def generate_for_donation_item(self, donation_item: "DonationItem") -> "DonationItem":
        """
        Generate and save QR code for a donation item.

        Args:
            donation_item: DonationItem instance

        Returns:
            DonationItem instance with QR code saved
        """
        frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
        scan_url = f"{frontend_url}/scan/donation-item/{donation_item.id}"

        # Generate or get access code
        if not donation_item.access_code:
            from ..models import DonationItem

            donation_item.access_code = generate_unique_code(DonationItem, "access_code")
            donation_item.save(update_fields=["access_code"])

        # Generate QR code image
        qr_img = self.generate_qr_code_image(scan_url)

        # Add access code text below QR code
        qr_img = self._add_code_text(qr_img, donation_item.access_code)

        # Convert to BytesIO for saving
        buffer = BytesIO()
        qr_img.save(buffer, format="PNG")
        buffer.seek(0)

        filename = f"donation_item_qr_{donation_item.id}.png"
        # Delete old QR code if it exists
        if donation_item.qr_code:
            donation_item.qr_code.delete(save=False)
        # Save the QR code file
        donation_item.qr_code.save(filename, File(buffer), save=True)
        donation_item.refresh_from_db()

        return donation_item

    def _add_code_text(
        self, img: qrcode.image.pil.PilImage, code: str
    ) -> qrcode.image.pil.PilImage:
        """
        Add access code text below the QR code image.

        Args:
            img: The QR code PIL image
            code: The access code to display

        Returns:
            PIL Image with code text added
        """
        # Convert to RGB if needed
        if img.mode != "RGB":
            img = img.convert("RGB")

        # Calculate dimensions
        qr_width, qr_height = img.size
        padding = 20
        text_height = 40
        new_height = qr_height + padding + text_height

        # Create new image with space for text
        new_img = Image.new("RGB", (qr_width, new_height), "white")
        new_img.paste(img, (0, 0))

        # Draw text
        draw = ImageDraw.Draw(new_img)

        # Try to use a nice font
        try:
            font_size = 32
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size
            )
        except OSError:
            try:
                font = ImageFont.load_default()
            except Exception:
                font = None

        # Calculate text position (centered)
        if font:
            bbox = draw.textbbox((0, 0), code, font=font)
            text_width = bbox[2] - bbox[0]
        else:
            text_width = len(code) * 20

        text_x = (qr_width - text_width) // 2
        text_y = qr_height + padding

        # Draw text
        draw.text(
            (text_x, text_y),
            code,
            fill="black",
            font=font if font else None,
        )

        return new_img

    def generate_sticker_sheet(
        self, donation_items: list["DonationItem"], sticker_size_px: int = 540
    ) -> Image.Image:
        """
        Generate a printable sticker sheet for donation items.

        Avery 95287: 1.5" x 1.5" labels, 30 per sheet (3 columns x 10 rows)
        At 300 DPI: 1.5" = 450 pixels, but we'll use 540px for better quality

        Args:
            donation_items: List of DonationItem instances to generate stickers for
            sticker_size_px: Size of each sticker in pixels (default 540 for 1.5" at 360 DPI)

        Returns:
            PIL Image containing the sticker sheet
        """
        # Avery 95287: 3 columns x 10 rows = 30 stickers per sheet
        # Standard US Letter size: 8.5" x 11" = 2550 x 3300 pixels at 300 DPI
        # But we'll use a more reasonable size for printing
        dpi = 300
        sheet_width = int(8.5 * dpi)  # 2550 pixels
        sheet_height = int(11 * dpi)  # 3300 pixels
        columns = 3
        rows = 10
        stickers_per_sheet = columns * rows

        # Calculate spacing
        # Leave margins: 0.25" top/bottom, 0.5" left/right
        margin_x = int(0.5 * dpi)  # 150 pixels
        margin_y = int(0.25 * dpi)  # 75 pixels
        available_width = sheet_width - (2 * margin_x)
        available_height = sheet_height - (2 * margin_y)

        # Calculate sticker size to fit
        sticker_width = (available_width - ((columns - 1) * int(0.125 * dpi))) // columns
        sticker_height = (available_height - ((rows - 1) * int(0.125 * dpi))) // rows

        # Create sheet image
        sheet = Image.new("RGB", (sheet_width, sheet_height), "white")
        draw = ImageDraw.Draw(sheet)

        # Generate stickers for each item
        for idx, item in enumerate(donation_items[:stickers_per_sheet]):
            # Generate QR code if not already generated
            if not item.qr_code:
                self.generate_for_donation_item(item)

            # Calculate position
            col = idx % columns
            row = idx // columns

            x = margin_x + (col * (sticker_width + int(0.125 * dpi)))
            y = margin_y + (row * (sticker_height + int(0.125 * dpi)))

            # Load QR code image
            if item.qr_code:
                try:
                    qr_img = Image.open(item.qr_code.path)
                    # Resize to fit sticker
                    qr_img.thumbnail(
                        (sticker_width - 20, sticker_height - 40), Image.Resampling.LANCZOS
                    )
                    # Center on sticker
                    qr_x = x + ((sticker_width - qr_img.width) // 2)
                    qr_y = y + 10
                    sheet.paste(qr_img, (qr_x, qr_y))

                    # Add donation number and item name (truncated)
                    try:
                        font = ImageFont.truetype(
                            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12
                        )
                        bold_font = ImageFont.truetype(
                            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 10
                        )
                    except OSError:
                        font = ImageFont.load_default()
                        bold_font = font

                    # Draw donation number (small, top)
                    donation_num = item.donation.donation_number
                    text_y = qr_y + qr_img.height + 5
                    draw.text(
                        (x + 5, text_y),
                        donation_num,
                        fill="black",
                        font=bold_font if bold_font else None,
                    )

                    # Draw item name (truncated if too long)
                    item_name = item.name[:20] + "..." if len(item.name) > 20 else item.name
                    text_y += 15
                    draw.text(
                        (x + 5, text_y),
                        item_name,
                        fill="black",
                        font=font if font else None,
                    )
                except Exception:
                    # If QR code can't be loaded, just draw a placeholder
                    draw.rectangle(
                        [(x, y), (x + sticker_width, y + sticker_height)],
                        outline="black",
                        width=2,
                    )
                    draw.text(
                        (x + 10, y + 10),
                        f"Item {item.id}",
                        fill="black",
                    )

        return sheet
