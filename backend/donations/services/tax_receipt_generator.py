"""
Tax receipt PDF generator for 501(c)(3) donation certificates.

Generates professional PDF tax receipts with all required information
for tax deduction purposes.
"""

from __future__ import annotations

import logging
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

from customization.models import SiteSettings
from donations.models import Donation, TaxReceipt

logger = logging.getLogger(__name__)


class TaxReceiptPDFGenerator:
    """Generate professional 501(c)(3) tax receipt PDFs."""

    PAGE_WIDTH, PAGE_HEIGHT = letter
    MARGIN = 0.75 * inch
    CONTENT_WIDTH = PAGE_WIDTH - (2 * MARGIN)

    def __init__(self, is_copy: bool = False) -> None:
        """Initialize the PDF generator.

        Args:
            is_copy: If True, add watermark to indicate this is a copy/reprint
        """
        self.is_copy = is_copy
        self.styles = getSampleStyleSheet()
        self._setup_custom_styles()

    def _setup_custom_styles(self) -> None:
        """Setup custom paragraph styles for the receipt."""
        self.title_style = ParagraphStyle(
            name="ReceiptTitle",
            parent=self.styles["Heading1"],
            fontSize=18,
            textColor=colors.HexColor("#000000"),
            alignment=TA_CENTER,
            spaceAfter=12,
        )
        self.heading_style = ParagraphStyle(
            name="ReceiptHeading",
            parent=self.styles["Heading2"],
            fontSize=12,
            textColor=colors.HexColor("#000000"),
            alignment=TA_LEFT,
            spaceAfter=6,
        )
        self.body_style = ParagraphStyle(
            name="ReceiptBody",
            parent=self.styles["Normal"],
            fontSize=10,
            textColor=colors.HexColor("#000000"),
            alignment=TA_LEFT,
            spaceAfter=6,
        )
        self.footer_style = ParagraphStyle(
            name="ReceiptFooter",
            parent=self.styles["Normal"],
            fontSize=9,
            textColor=colors.HexColor("#666666"),
            alignment=TA_CENTER,
        )

    def generate_pdf(
        self, donation: Donation, tax_receipt: TaxReceipt, signature_user=None
    ) -> bytes:
        """Generate PDF for a tax receipt.

        Args:
            donation: The donation this receipt is for
            tax_receipt: The TaxReceipt instance
            signature_user: User whose signature to use (if available)

        Returns:
            PDF content as bytes
        """
        buffer = BytesIO()
        pdf_canvas = canvas.Canvas(buffer, pagesize=letter)
        pdf_canvas.setTitle(f"Tax Receipt {tax_receipt.serial_number}")
        pdf_canvas.setSubject("501(c)(3) Donation Tax Receipt")

        # Get organization settings
        site_settings = SiteSettings.get()

        # Draw watermark if this is a copy
        if self.is_copy:
            self._draw_watermark(pdf_canvas, "COPY")

        # Draw header
        y_position = self._draw_header(pdf_canvas, site_settings)

        # Draw receipt title
        y_position = self._draw_title(pdf_canvas, y_position)

        # Draw organization information
        y_position = self._draw_organization_info(pdf_canvas, site_settings, y_position)

        # Draw donor information
        y_position = self._draw_donor_info(pdf_canvas, donation, y_position)

        # Draw donation details
        y_position = self._draw_donation_details(pdf_canvas, donation, tax_receipt, y_position)

        # Draw items list
        y_position = self._draw_items_list(pdf_canvas, donation, y_position)

        # Draw tax language
        y_position = self._draw_tax_language(pdf_canvas, y_position)

        # Draw signature
        y_position = self._draw_signature(pdf_canvas, site_settings, signature_user, y_position)

        # Draw footer
        self._draw_footer(pdf_canvas, tax_receipt)

        pdf_canvas.save()
        buffer.seek(0)
        return buffer.getvalue()

    def _draw_watermark(self, pdf_canvas: canvas.Canvas, text: str) -> None:
        """Draw watermark on the page."""
        pdf_canvas.saveState()
        pdf_canvas.setFont("Helvetica-Bold", 72)
        pdf_canvas.setFillColor(colors.HexColor("#CCCCCC"), alpha=0.3)
        pdf_canvas.rotate(45)
        # Center the watermark
        x = self.PAGE_WIDTH / 2
        y = self.PAGE_HEIGHT / 2
        pdf_canvas.drawCentredString(x, y, text)
        pdf_canvas.restoreState()

    def _draw_header(self, pdf_canvas: canvas.Canvas, site_settings: SiteSettings) -> float:
        """Draw header with organization name."""
        y = self.PAGE_HEIGHT - self.MARGIN
        pdf_canvas.setFont("Helvetica-Bold", 16)
        pdf_canvas.drawCentredString(self.PAGE_WIDTH / 2, y, site_settings.site_name)
        return y - 30

    def _draw_title(self, pdf_canvas: canvas.Canvas, y: float) -> float:
        """Draw receipt title."""
        y -= 20
        pdf_canvas.setFont("Helvetica-Bold", 14)
        pdf_canvas.drawCentredString(self.PAGE_WIDTH / 2, y, "501(c)(3) DONATION TAX RECEIPT")
        return y - 20

    def _draw_organization_info(
        self, pdf_canvas: canvas.Canvas, site_settings: SiteSettings, y: float
    ) -> float:
        """Draw organization information."""
        y -= 15
        pdf_canvas.setFont("Helvetica", 10)

        # Organization address
        if site_settings.organization_address:
            lines = site_settings.organization_address.split("\n")
            for line in lines:
                if line.strip():
                    pdf_canvas.drawString(self.MARGIN, y, line.strip())
                    y -= 12

        # EIN
        if site_settings.organization_ein:
            y -= 5
            pdf_canvas.drawString(self.MARGIN, y, f"EIN: {site_settings.organization_ein}")
            y -= 12

        return y - 10

    def _draw_donor_info(self, pdf_canvas: canvas.Canvas, donation: Donation, y: float) -> float:
        """Draw donor information."""
        y -= 15
        pdf_canvas.setFont("Helvetica-Bold", 11)
        pdf_canvas.drawString(self.MARGIN, y, "DONOR INFORMATION:")
        y -= 15

        pdf_canvas.setFont("Helvetica", 10)
        pdf_canvas.drawString(self.MARGIN, y, f"Name: {donation.donor_name}")
        y -= 12

        if donation.donor_address:
            lines = donation.donor_address.split("\n")
            for line in lines:
                if line.strip():
                    pdf_canvas.drawString(self.MARGIN, y, line.strip())
                    y -= 12

        if donation.donor_email:
            pdf_canvas.drawString(self.MARGIN, y, f"Email: {donation.donor_email}")
            y -= 12

        return y - 10

    def _draw_donation_details(
        self,
        pdf_canvas: canvas.Canvas,
        donation: Donation,
        tax_receipt: TaxReceipt,
        y: float,
    ) -> float:
        """Draw donation details."""
        y -= 15
        pdf_canvas.setFont("Helvetica-Bold", 11)
        pdf_canvas.drawString(self.MARGIN, y, "DONATION DETAILS:")
        y -= 15

        pdf_canvas.setFont("Helvetica", 10)
        pdf_canvas.drawString(
            self.MARGIN,
            y,
            f"Date of Donation: {donation.date_received.strftime('%B %d, %Y')}",
        )
        y -= 12

        pdf_canvas.drawString(
            self.MARGIN,
            y,
            f"Receipt Serial Number: {str(tax_receipt.serial_number)}",
        )
        y -= 12

        if donation.estimated_value:
            pdf_canvas.setFont("Helvetica-Bold", 11)
            pdf_canvas.drawString(
                self.MARGIN,
                y,
                f"Estimated Value: ${donation.estimated_value:,.2f}",
            )
            y -= 15
            pdf_canvas.setFont("Helvetica", 10)

        return y - 10

    def _draw_items_list(self, pdf_canvas: canvas.Canvas, donation: Donation, y: float) -> float:
        """Draw list of donated items."""
        items = donation.items.all()
        if not items.exists():
            return y

        y -= 15
        pdf_canvas.setFont("Helvetica-Bold", 11)
        pdf_canvas.drawString(self.MARGIN, y, "ITEMS DONATED:")
        y -= 15

        pdf_canvas.setFont("Helvetica", 9)
        for item in items:
            # Item name and quantity
            item_text = f"• {item.name}"
            if item.quantity > 1:
                item_text += f" (Quantity: {item.quantity})"
            pdf_canvas.drawString(self.MARGIN + 10, y, item_text)
            y -= 12

            # Item description if available
            if item.description:
                # Wrap long descriptions
                desc_lines = self._wrap_text(item.description, 80)
                for line in desc_lines:
                    pdf_canvas.drawString(self.MARGIN + 20, y, line)
                    y -= 10

            y -= 3  # Space between items

        return y - 10

    def _draw_tax_language(self, pdf_canvas: canvas.Canvas, y: float) -> float:
        """Draw standard 501(c)(3) tax deduction language."""
        y -= 15
        pdf_canvas.setFont("Helvetica-Bold", 10)
        pdf_canvas.drawString(self.MARGIN, y, "TAX DEDUCTION INFORMATION:")
        y -= 15

        pdf_canvas.setFont("Helvetica", 9)
        tax_text = (
            "No goods or services were provided in exchange for this contribution. "
            "This organization is a tax-exempt organization under Section 501(c)(3) "
            "of the Internal Revenue Code. Your contribution is tax-deductible to the "
            "extent allowed by law. Please consult with your tax advisor regarding "
            "the deductibility of your contribution."
        )

        # Wrap and draw tax text
        lines = self._wrap_text(tax_text, 90)
        for line in lines:
            pdf_canvas.drawString(self.MARGIN, y, line)
            y -= 10

        return y - 15

    def _draw_signature(
        self,
        pdf_canvas: canvas.Canvas,
        site_settings: SiteSettings,
        signature_user,
        y: float,
    ) -> float:
        """Draw signature section."""
        y -= 10
        pdf_canvas.setFont("Helvetica", 10)

        # Signature line
        line_y = y - 30
        pdf_canvas.line(self.MARGIN, line_y, self.MARGIN + 200, line_y)

        # Signature image if available
        if signature_user and signature_user.signature_image:
            try:
                signature_file = signature_user.signature_image
                # Try to get path, fall back to opening file
                try:
                    signature_path = signature_file.path
                except (AttributeError, NotImplementedError):
                    # For storage backends that don't support .path, open the file
                    signature_file.open()
                    signature_path = signature_file
                    # Use ImageReader for file-like objects
                    from reportlab.lib.utils import ImageReader

                    signature_path = ImageReader(signature_path)

                img_width = 150
                img_height = 50
                pdf_canvas.drawImage(
                    signature_path,
                    self.MARGIN,
                    line_y - 5,
                    width=img_width,
                    height=img_height,
                    preserveAspectRatio=True,
                )
            except Exception as e:
                logger.warning(f"Could not load signature image: {e}")

        # Signature name and title
        name_y = line_y - 20
        # Use signature user's full name if available, otherwise fall back to site settings
        signature_name = None
        if signature_user:
            # Try to get full name from user
            full_name = signature_user.get_full_name()
            if full_name:
                signature_name = full_name
            elif signature_user.first_name or signature_user.last_name:
                # Fall back to combining first and last name if get_full_name() returns empty
                signature_name = f"{signature_user.first_name} {signature_user.last_name}".strip()

        # Use site settings name if no signature user name available
        if not signature_name and site_settings.authorized_signature_name:
            signature_name = site_settings.authorized_signature_name

        if signature_name:
            pdf_canvas.drawString(self.MARGIN, name_y, signature_name)
        if site_settings.authorized_signature_title:
            pdf_canvas.setFont("Helvetica", 9)
            pdf_canvas.drawString(
                self.MARGIN, name_y - 12, site_settings.authorized_signature_title
            )

        return name_y - 30

    def _draw_footer(self, pdf_canvas: canvas.Canvas, tax_receipt: TaxReceipt) -> None:
        """Draw footer with receipt number."""
        pdf_canvas.setFont("Helvetica", 8)
        footer_text = f"Receipt Serial Number: {tax_receipt.serial_number} | Issued: {tax_receipt.issued_date.strftime('%Y-%m-%d')}"
        pdf_canvas.drawCentredString(self.PAGE_WIDTH / 2, 30, footer_text)

    def _wrap_text(self, text: str, max_chars: int) -> list[str]:
        """Wrap text to fit within max_chars per line."""
        words = text.split()
        lines = []
        current_line = ""

        for word in words:
            if len(current_line) + len(word) + 1 <= max_chars:
                if current_line:
                    current_line += " " + word
                else:
                    current_line = word
            else:
                if current_line:
                    lines.append(current_line)
                current_line = word

        if current_line:
            lines.append(current_line)

        return lines

    def generate_and_save(
        self, donation: Donation, tax_receipt: TaxReceipt, signature_user=None
    ) -> str:
        """Generate PDF and save to storage.

        Args:
            donation: The donation
            tax_receipt: The tax receipt instance
            signature_user: User whose signature to use

        Returns:
            Storage path of saved PDF
        """
        pdf_bytes = self.generate_pdf(donation, tax_receipt, signature_user)

        # Generate filename
        filename = f"tax_receipt_{tax_receipt.serial_number}.pdf"
        if self.is_copy:
            filename = f"tax_receipt_{tax_receipt.serial_number}_copy.pdf"

        storage_path = f"donations/tax_receipts/{filename}"

        # Delete existing file if it exists
        if default_storage.exists(storage_path):
            default_storage.delete(storage_path)

        # Save new file
        saved_path = default_storage.save(storage_path, ContentFile(pdf_bytes))

        return saved_path
