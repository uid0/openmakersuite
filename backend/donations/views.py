"""
Views for donations API.
"""

from io import BytesIO

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone

from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .models import Disposition, Donation, DonationItem
from .serializers import (
    DispositionSerializer,
    DonationItemSerializer,
    DonationListSerializer,
    DonationSerializer,
)
from .services.qr_code_service import DonationItemQRCodeService
from .services.reporting_service import DonationReportingService


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
                {"error": "No items in this donation"}, status=status.HTTP_400_BAD_REQUEST
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
