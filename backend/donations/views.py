"""
Views for donations API.
"""

import logging
from io import BytesIO

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.http import HttpResponse
from django.utils import timezone

from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from .models import Disposition, Donation, DonationItem, TaxReceipt
from .serializers import (
    DispositionSerializer,
    DonationItemSerializer,
    DonationListSerializer,
    DonationSerializer,
    TaxReceiptSerializer,
)
from .services.email_service import DonationEmailService
from .services.qr_code_service import DonationItemQRCodeService
from .services.reporting_service import DonationReportingService
from .services.tax_receipt_generator import TaxReceiptPDFGenerator

User = get_user_model()
logger = logging.getLogger(__name__)


class DonationViewSet(viewsets.ModelViewSet):
    """ViewSet for Donation operations."""

    queryset = Donation.objects.all().order_by("-date_received", "-created_at")
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        """Use list serializer for list view, full serializer for detail."""
        if self.action == "list":
            return DonationListSerializer
        return DonationSerializer

    def get_queryset(self):
        """Filter queryset based on query parameters."""
        queryset = super().get_queryset()
        status_filter = self.request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        return queryset.select_related("received_by", "reviewed_by").prefetch_related("items")

    @action(detail=True, methods=["post"])
    def generate_sticker_sheet(self, request, pk=None):
        """Generate printable sticker sheet for all items in a donation."""
        donation = self.get_object()
        items = donation.items.all()

        if not items.exists():
            return Response(
                {"error": "No items in this donation"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Generate QR codes for items that don't have them
        qr_service = DonationItemQRCodeService()
        for item in items:
            if not item.access_code:
                qr_service.generate_for_donation_item(item)

        # Generate sticker sheet
        sticker_sheet = qr_service.generate_sticker_sheet(list(items))

        # Convert to response
        buffer = BytesIO()
        sticker_sheet.save(buffer, format="PNG", dpi=(300, 300))
        buffer.seek(0)

        response = HttpResponse(buffer.getvalue(), content_type="image/png")
        response["Content-Disposition"] = (
            f'attachment; filename="donation_stickers_{donation.donation_number}.png"'
        )
        return response

    @action(detail=False, methods=["get"])
    def quarterly_report(self, request):
        """Generate quarterly donation report."""
        year = int(request.query_params.get("year", timezone.now().year))
        quarter = request.query_params.get("quarter")
        quarter = int(quarter) if quarter else None

        report = DonationReportingService.get_quarterly_report(year, quarter)
        return Response(report)

    @action(detail=False, methods=["get"])
    def yearly_report(self, request):
        """Generate yearly donation report."""
        year = int(request.query_params.get("year", timezone.now().year))
        report = DonationReportingService.get_yearly_report(year)
        return Response(report)

    @action(detail=True, methods=["post"])
    def generate_tax_receipt(self, request, pk=None):
        """Manually trigger tax receipt generation."""
        donation = self.get_object()

        # Check if receipt already exists
        if donation.tax_receipts.exists():
            return Response(
                {"error": "Tax receipt already exists for this donation"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Verify password/passkey if signature is being used
        signature_user_id = request.data.get("signature_user_id")
        password = request.data.get("password")

        signature_user = None
        if signature_user_id:
            try:
                signature_user = User.objects.get(id=signature_user_id)
                if signature_user.signature_image:
                    # Require password verification
                    if not password:
                        return Response(
                            {"error": "Password required to use signature"},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    # Verify password
                    if not authenticate(username=signature_user.username, password=password):
                        return Response(
                            {"error": "Invalid password for signature user"},
                            status=status.HTTP_401_UNAUTHORIZED,
                        )
            except User.DoesNotExist:
                return Response(
                    {"error": "Signature user not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )

        try:
            # Create tax receipt
            tax_receipt = TaxReceipt.objects.create(
                donation=donation,
                issued_by=request.user,
                is_copy=False,
            )

            # Generate PDF
            generator = TaxReceiptPDFGenerator(is_copy=False)
            pdf_path = generator.generate_and_save(
                donation, tax_receipt, signature_user=signature_user or request.user
            )

            # Update tax receipt with PDF path
            tax_receipt.pdf_file = pdf_path
            tax_receipt.save()

            # Update donation
            donation.tax_receipt_issued = True
            donation.tax_receipt_number = str(tax_receipt.serial_number)
            donation.tax_receipt_issued_at = tax_receipt.issued_date
            donation.save(
                update_fields=[
                    "tax_receipt_issued",
                    "tax_receipt_number",
                    "tax_receipt_issued_at",
                ]
            )

            # Send email to donor
            if donation.donor_email:
                try:
                    from django.core.files.storage import default_storage

                    if default_storage.exists(pdf_path):
                        full_path = default_storage.path(pdf_path)
                        DonationEmailService.send_tax_receipt_email(
                            donation, tax_receipt, pdf_path=full_path
                        )
                except Exception as e:
                    logger.error(f"Failed to send tax receipt email: {e}")

            serializer = TaxReceiptSerializer(tax_receipt)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.error(f"Failed to generate tax receipt: {e}")
            return Response(
                {"error": f"Failed to generate tax receipt: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["get"])
    def download_tax_receipt(self, request, pk=None):
        """Download tax receipt PDF."""
        donation = self.get_object()

        # Get the original receipt (not a copy)
        tax_receipt = donation.tax_receipts.filter(is_copy=False).first()

        if not tax_receipt:
            return Response(
                {"error": "Tax receipt not found for this donation"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Check if copy is requested
        is_copy = request.query_params.get("copy", "false").lower() == "true"

        try:
            # Generate PDF
            generator = TaxReceiptPDFGenerator(is_copy=is_copy)
            pdf_bytes = generator.generate_pdf(
                donation, tax_receipt, signature_user=tax_receipt.issued_by
            )

            filename = f"tax_receipt_{tax_receipt.serial_number}.pdf"
            if is_copy:
                filename = f"tax_receipt_{tax_receipt.serial_number}_copy.pdf"

            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            return response

        except Exception as e:
            logger.error(f"Failed to generate tax receipt PDF: {e}")
            return Response(
                {"error": f"Failed to generate PDF: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DonationItemViewSet(viewsets.ModelViewSet):
    """ViewSet for DonationItem operations."""

    queryset = DonationItem.objects.all()
    serializer_class = DonationItemSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Filter by donation if provided."""
        queryset = super().get_queryset()
        donation_id = self.request.query_params.get("donation")
        if donation_id:
            queryset = queryset.filter(donation_id=donation_id)
        return queryset.select_related("donation", "asset", "inventory_item")

    @action(detail=True, methods=["post"])
    def generate_qr_code(self, request, pk=None):
        """Generate QR code for this donation item."""
        item = self.get_object()
        qr_service = DonationItemQRCodeService()
        qr_service.generate_for_donation_item(item, require_access_code=False)
        serializer = self.get_serializer(item)
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def download_label(self, request, pk=None):
        """Generate and download a 2x2\" label PDF for a donation item."""
        item = self.get_object()

        from ..utils.label_generator import DonationLabelRenderer

        try:
            renderer = DonationLabelRenderer()
            pdf_bytes = renderer.render_label(item)
            filename = f"donation_label_{item.id}.pdf"

            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            return response
        except Exception as e:
            return Response(
                {"error": f"Failed to generate label: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DispositionViewSet(viewsets.ModelViewSet):
    """ViewSet for Disposition operations."""

    queryset = Disposition.objects.all().order_by("-disposition_date", "-created_at")
    serializer_class = DispositionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Filter by donation item if provided."""
        queryset = super().get_queryset()
        item_id = self.request.query_params.get("donation_item")
        if item_id:
            queryset = queryset.filter(donation_item_id=item_id)
        return queryset.select_related(
            "donation_item", "donation_item__donation", "disposed_by", "created_asset"
        )


class TaxReceiptViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet for TaxReceipt operations."""

    queryset = TaxReceipt.objects.all().order_by("-issued_date", "-created_at")
    serializer_class = TaxReceiptSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Filter queryset based on permissions."""
        queryset = super().get_queryset()

        # Non-admin users can only see receipts they issued
        if not self.request.user.is_staff:
            queryset = queryset.filter(issued_by=self.request.user)

        # Filter by serial number if provided (for public lookup)
        serial_number = self.request.query_params.get("serial_number")
        if serial_number:
            queryset = queryset.filter(serial_number=serial_number)

        return queryset.select_related("donation", "issued_by")

    @action(detail=True, methods=["get"])
    def download_pdf(self, request, pk=None):
        """Download tax receipt PDF."""
        tax_receipt = self.get_object()

        # Check if copy is requested
        is_copy = request.query_params.get("copy", "false").lower() == "true"

        try:
            # Generate PDF
            generator = TaxReceiptPDFGenerator(is_copy=is_copy)
            pdf_bytes = generator.generate_pdf(
                tax_receipt.donation, tax_receipt, signature_user=tax_receipt.issued_by
            )

            filename = f"tax_receipt_{tax_receipt.serial_number}.pdf"
            if is_copy:
                filename = f"tax_receipt_{tax_receipt.serial_number}_copy.pdf"

            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            return response

        except Exception as e:
            logger.error(f"Failed to generate tax receipt PDF: {e}")
            return Response(
                {"error": f"Failed to generate PDF: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["post"], permission_classes=[IsAdminUser])
    def regenerate(self, request, pk=None):
        """Regenerate tax receipt PDF (admin only)."""
        tax_receipt = self.get_object()

        # Verify password/passkey if signature is being used
        signature_user_id = request.data.get("signature_user_id")
        password = request.data.get("password")

        signature_user = None
        if signature_user_id:
            try:
                signature_user = User.objects.get(id=signature_user_id)
                if signature_user.signature_image:
                    # Require password verification
                    if not password:
                        return Response(
                            {"error": "Password required to use signature"},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    # Verify password
                    if not authenticate(username=signature_user.username, password=password):
                        return Response(
                            {"error": "Invalid password for signature user"},
                            status=status.HTTP_401_UNAUTHORIZED,
                        )
            except User.DoesNotExist:
                return Response(
                    {"error": "Signature user not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )

        try:
            # Generate PDF
            generator = TaxReceiptPDFGenerator(is_copy=tax_receipt.is_copy)
            pdf_path = generator.generate_and_save(
                tax_receipt.donation,
                tax_receipt,
                signature_user=signature_user or tax_receipt.issued_by,
            )

            # Update tax receipt with PDF path
            tax_receipt.pdf_file = pdf_path
            tax_receipt.save()

            serializer = TaxReceiptSerializer(tax_receipt)
            return Response(serializer.data)

        except Exception as e:
            logger.error(f"Failed to regenerate tax receipt: {e}")
            return Response(
                {"error": f"Failed to regenerate PDF: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def upload_signature(request):
    """
    Upload signature image for the authenticated user.
    Requires password verification to ensure only the user can upload their signature.
    """
    password = request.data.get("password")
    signature_file = request.FILES.get("signature")

    if not password:
        return Response(
            {"error": "Password is required to upload signature"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not signature_file:
        return Response(
            {"error": "Signature file is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Verify password
    user = request.user
    if not authenticate(username=user.username, password=password):
        return Response(
            {"error": "Invalid password"},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    # Validate file type (PNG only)
    if not signature_file.name.lower().endswith(".png"):
        return Response(
            {"error": "Signature must be a PNG file"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        # Save signature
        user.signature_image = signature_file
        from django.utils import timezone

        user.signature_uploaded_at = timezone.now()
        user.save(update_fields=["signature_image", "signature_uploaded_at"])

        return Response(
            {
                "message": "Signature uploaded successfully",
                "signature_url": (user.signature_image.url if user.signature_image else None),
            },
            status=status.HTTP_201_CREATED,
        )
    except Exception as e:
        logger.error(f"Failed to upload signature: {e}")
        return Response(
            {"error": f"Failed to upload signature: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


# Public lookup endpoint for tax receipts
@api_view(["GET"])
@permission_classes([AllowAny])
def lookup_tax_receipt(request):
    """
    Public lookup endpoint for tax receipts by serial number.
    No authentication required.
    """
    serial_number = request.query_params.get("serial_number", "").strip()

    if not serial_number:
        return Response(
            {"error": "serial_number parameter is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        tax_receipt = TaxReceipt.objects.select_related("donation", "issued_by").get(
            serial_number=serial_number
        )
        serializer = TaxReceiptSerializer(tax_receipt)
        return Response(serializer.data)
    except TaxReceipt.DoesNotExist:
        return Response(
            {"error": "Tax receipt not found"},
            status=status.HTTP_404_NOT_FOUND,
        )


# Lookup endpoint for QR code scanning
@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def lookup_donation_item_by_code(request):
    """
    Look up a donation item by access code (for QR code scanning).

    Accepts GET or POST with 'code' parameter.
    Returns the donation item with its details.
    """
    code = request.data.get("code") or request.query_params.get("code", "").strip().upper()

    if not code:
        return Response(
            {"error": "Code parameter is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Validate code format (6 characters, alphanumeric)
    if len(code) != 6:
        return Response(
            {"error": "Code must be exactly 6 characters"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Try to find the donation item
    try:
        item = DonationItem.objects.select_related("donation").get(access_code=code)
        frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")

        return Response(
            {
                "type": "donation_item",
                "id": str(item.id),
                "name": item.name,
                "donation_number": item.donation.donation_number,
                "donor_name": item.donation.donor_name,
                "status": item.status,
                "condition": item.condition,
                "url": f"{frontend_url}/scan/donation-item/{item.id}",
            }
        )
    except DonationItem.DoesNotExist:
        return Response(
            {"error": "Code not found"},
            status=status.HTTP_404_NOT_FOUND,
        )
