"""
Brother QL-820NWB ESC/P command generator for direct printing.

This module generates raw ESC/P commands that can be sent directly to
Brother QL-820NWB printers for label printing.
"""

from __future__ import annotations

from io import BytesIO
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from inventory.models import Asset


class BrotherESCPGenerator:
    """Generate ESC/P commands for Brother QL-820NWB printers."""

    # Brother QL-820NWB specifications
    # Label width: 62mm (2.44 inches) = 236 dots at 96 dpi, 732 dots at 300 dpi
    # Printer resolution: 300 x 600 dpi
    LABEL_WIDTH_MM = 62
    LABEL_HEIGHT_MM = 62  # Continuous feed, can be any height
    DPI_HORIZONTAL = 300
    DPI_VERTICAL = 600

    # Convert mm to dots
    LABEL_WIDTH_DOTS = int((LABEL_WIDTH_MM / 25.4) * DPI_HORIZONTAL)  # ~732 dots
    LABEL_HEIGHT_DOTS = int((LABEL_HEIGHT_MM / 25.4) * DPI_VERTICAL)  # ~1464 dots

    def __init__(self, base_url: Optional[str] = None) -> None:
        """Initialize the ESC/P generator.

        Args:
            base_url: Base URL for QR code links (defaults to settings)
        """
        from django.conf import settings

        self.base_url = base_url or getattr(settings, "FRONTEND_URL", "http://localhost:3000")

    def generate_label_commands(self, asset: Asset) -> bytes:
        """Generate ESC/P commands for printing a label.

        Args:
            asset: The asset to generate a label for

        Returns:
            ESC/P command bytes ready to send to printer
        """
        # Ensure QR code exists
        if not asset.qr_code:
            from .qr_generator import save_qr_code_to_asset

            save_qr_code_to_asset(asset)
            asset.refresh_from_db()

        # Create image for the label
        label_image = self._create_label_image(asset)

        # Convert image to ESC/P commands
        escp_commands = self._image_to_escp(label_image)

        return escp_commands

    def _create_label_image(self, asset: Asset) -> Image.Image:
        """Create a PIL Image for the label.

        Args:
            asset: Asset to create label for

        Returns:
            PIL Image object
        """
        # Create image at printer resolution
        img = Image.new("RGB", (self.LABEL_WIDTH_DOTS, self.LABEL_HEIGHT_DOTS), "white")
        draw = ImageDraw.Draw(img)

        # Try to load a font, fallback to default if not available
        try:
            # Try to use a larger font
            font_large = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 60
            )
            font_medium = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 40)
            font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 30)
        except OSError:
            # Fallback to default font
            try:
                font_large = ImageFont.load_default()
                font_medium = ImageFont.load_default()
                font_small = ImageFont.load_default()
            except Exception:
                font_large = None
                font_medium = None
                font_small = None

        margin = 50  # dots
        qr_size = 400  # dots (larger for better scanning)

        # Draw QR code on the left
        if asset.qr_code:
            try:
                asset.qr_code.open()
                qr_img = Image.open(asset.qr_code)
                qr_img = qr_img.convert("RGB")
                # Resize QR code
                qr_img = qr_img.resize((qr_size, qr_size), Image.Resampling.LANCZOS)
                # Paste QR code
                img.paste(qr_img, (margin, margin))
                asset.qr_code.close()
            except Exception:
                # Draw placeholder if QR code can't be loaded
                draw.rectangle(
                    [(margin, margin), (margin + qr_size, margin + qr_size)],
                    fill="gray",
                    outline="black",
                    width=2,
                )

        # Draw text on the right
        text_x = margin + qr_size + 30
        text_y = margin

        # Asset name
        name_text = asset.name[:30]  # Limit length
        if font_large:
            draw.text((text_x, text_y), name_text, fill="black", font=font_large)
        else:
            draw.text((text_x, text_y), name_text, fill="black")

        # Asset tag
        if asset.asset_tag:
            tag_y = text_y + 80
            tag_text = f"Tag: {asset.asset_tag}"
            if font_medium:
                draw.text((text_x, tag_y), tag_text, fill="darkgray", font=font_medium)
            else:
                draw.text((text_x, tag_y), tag_text, fill="darkgray")

        # Status
        status_y = tag_y + 60 if asset.asset_tag else text_y + 80
        status_text = asset.get_status_display()
        status_color = "green" if asset.status == Asset.ACTIVE else "orange"
        if font_medium:
            draw.text((text_x, status_y), status_text, fill=status_color, font=font_medium)
        else:
            draw.text((text_x, status_y), status_text, fill=status_color)

        # Location
        if asset.location:
            location_y = status_y + 60
            location_text = f"Loc: {asset.location.name[:20]}"
            if font_small:
                draw.text((text_x, location_y), location_text, fill="black", font=font_small)
            else:
                draw.text((text_x, location_y), location_text, fill="black")

        return img

    def _image_to_escp(self, image: Image.Image) -> bytes:
        """Convert PIL Image to Brother Raster Command format.

        Note: This generates a format compatible with Brother QL printers.
        For direct printing, you may need to use Brother's SDK or print server.

        Args:
            image: PIL Image to convert

        Returns:
            Raster command bytes (simplified format)
        """
        # Convert to 1-bit (black and white) for better printer compatibility
        img_bw = image.convert("1")
        width, height = img_bw.size

        # Brother QL printers use Raster Command Language
        # This is a simplified implementation - for production use,
        # consider using Brother's official SDK or bpl protocol
        commands = BytesIO()

        # Initialize printer
        commands.write(b"\x1B@")  # ESC @ - Initialize printer

        # Set media type: 62mm continuous tape
        # ESC i a - Set media type
        # a = 0x01 for 62mm width
        commands.write(b"\x1Bi\x01")

        # Set print quality and mode
        commands.write(b"\x1Bia\x01")  # Auto cut enabled
        commands.write(b"\x1Bic\x00")  # No special tape

        # Convert image to raster format
        # Brother expects data in rows, 8 pixels per byte
        for y in range(height):
            row_data = bytearray()
            for x in range(0, width, 8):
                byte_val = 0
                for bit in range(8):
                    if x + bit < width:
                        # Invert: 0 = black, 255 = white in PIL, but printer expects 1 = black
                        pixel = img_bw.getpixel((x + bit, y))
                        if pixel == 0:  # Black pixel
                            byte_val |= 1 << (7 - bit)
                row_data.append(byte_val)

            # Send raster row: ESC i m n1 n2 d1...dk
            # m = 0x01 (raster graphics mode)
            # n1, n2 = row width in bytes (little-endian, 2 bytes)
            row_bytes = len(row_data)
            commands.write(b"\x1Bi\x01")  # ESC i 0x01 (raster mode)
            commands.write(bytes([row_bytes & 0xFF, (row_bytes >> 8) & 0xFF]))
            commands.write(bytes(row_data))

        # Feed and cut label
        commands.write(b"\x1Bi\x01")  # Auto cut
        commands.write(b"\x0A")  # Line feed

        return commands.getvalue()

    def generate_label_file(self, asset: Asset, output_format: str = "escp") -> bytes:
        """Generate label file in specified format.

        Args:
            asset: Asset to generate label for
            output_format: Format ('escp' for raw commands, 'png' for image preview)

        Returns:
            File bytes
        """
        if output_format == "escp":
            return self.generate_label_commands(asset)
        elif output_format == "png":
            image = self._create_label_image(asset)
            buffer = BytesIO()
            image.save(buffer, format="PNG")
            return buffer.getvalue()
        else:
            raise ValueError(f"Unsupported format: {output_format}")
