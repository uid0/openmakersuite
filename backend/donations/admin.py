"""
Admin interface for donations app.
"""

from django.contrib import admin, messages
from django.http import HttpResponse

from .models import Disposition, Donation, DonationItem, TaxReceipt
from .services.tax_receipt_generator import TaxReceiptPDFGenerator


@admin.register(Donation)
class DonationAdmin(admin.ModelAdmin):
    """Admin interface for Donation model."""

    list_display = [
        "donation_number",
        "donor_name",
        "date_received",
        "status",
        "total_items",
        "total_quantity",
        "received_by",
        "created_at",
    ]
    list_filter = ["status", "date_received", "tax_receipt_issued"]
    search_fields = ["donation_number", "donor_name", "donor_email"]
    readonly_fields = [
        "donation_number",
        "created_at",
        "updated_at",
        "tax_receipt_issued_at",
    ]
    actions = [
        "generate_items_from_estimate",
        "download_label_sheet",
        "generate_tax_receipt",
        "download_tax_receipt_clean",
        "download_tax_receipt_copy",
    ]
    fieldsets = (
        (
            "Donation Information",
            {
                "fields": (
                    "donation_number",
                    "donor_name",
                    "donor_email",
                    "donor_phone",
                    "donor_address",
                )
            },
        ),
        (
            "Receipt Information",
            {
                "fields": (
                    "date_received",
                    "received_by",
                    "received_notes",
                )
            },
        ),
        (
            "Status and Review",
            {
                "fields": (
                    "status",
                    "reviewed_by",
                    "reviewed_at",
                    "review_notes",
                )
            },
        ),
        (
            "Item Processing",
            {
                "fields": ("estimated_number_of_items",),
                "description": "Enter estimated number of items to generate QR code labels",
            },
        ),
        (
            "Additional Information",
            {
                "fields": (
                    "estimated_value",
                    "associated_costs",
                    "cost_notes",
                    "tax_receipt_issued",
                    "tax_receipt_number",
                    "tax_receipt_issued_at",
                )
            },
        ),
        (
            "Metadata",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    @admin.action(description="Generate donation items from estimated count")
    def generate_items_from_estimate(self, request, queryset):
        """Generate DonationItem instances based on estimated_number_of_items."""
        from ..services.qr_code_service import DonationItemQRCodeService

        total_created = 0
        qr_service = DonationItemQRCodeService(include_logo=False)

        for donation in queryset:
            if not donation.estimated_number_of_items:
                self.message_user(
                    request,
                    f"Donation {donation.donation_number} has no estimated number of items.",
                    level=messages.WARNING,
                )
                continue

            # Count existing items
            existing_count = donation.items.count()
            needed = donation.estimated_number_of_items - existing_count

            if needed <= 0:
                self.message_user(
                    request,
                    f"Donation {donation.donation_number} already has {existing_count} items (estimated: {donation.estimated_number_of_items}).",
                    level=messages.INFO,
                )
                continue

            # Create the needed items
            for i in range(needed):
                item = DonationItem.objects.create(
                    donation=donation,
                    name=f"Item {existing_count + i + 1}",
                    description=f"Donation item {existing_count + i + 1} from {donation.donor_name}",
                    quantity=1,
                    status=DonationItem.PENDING_REVIEW,
                )
                # Generate QR code without access code
                qr_service.generate_for_donation_item(item, require_access_code=False)
                total_created += 1

            self.message_user(
                request,
                f"Created {needed} item(s) for donation {donation.donation_number}.",
                level=messages.SUCCESS,
            )

        if total_created > 0:
            self.message_user(
                request,
                f"Successfully created {total_created} donation item(s) with QR codes.",
                level=messages.SUCCESS,
            )

    @admin.action(description='Download 2x2" QR code label sheet')
    def download_label_sheet(self, request, queryset):
        """Generate and download 2x2\" label PDF for donation items."""
        from ..utils.label_generator import DonationLabelRenderer

        if queryset.count() != 1:
            self.message_user(
                request,
                "Please select exactly one donation.",
                level=messages.ERROR,
            )
            return

        donation = queryset.first()
        items = list(donation.items.all())

        if not items:
            self.message_user(
                request,
                f"No items found for donation {donation.donation_number}. Generate items first.",
                level=messages.ERROR,
            )
            return

        # Generate labels
        renderer = DonationLabelRenderer()
        pdf_bytes = renderer.render_batch(items)

        # Generate filename
        filename = f"donation_labels_{donation.donation_number}.pdf"

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'

        self.message_user(
            request,
            f"Generated label sheet for {len(items)} item(s). Download should start automatically.",
            level=messages.SUCCESS,
        )

        return response

    @admin.action(description="Generate Tax Receipt")
    def generate_tax_receipt(self, request, queryset):
        """Generate tax receipt for selected donations."""
        from donations.models import TaxReceipt
        from donations.services.email_service import DonationEmailService

        generated = 0
        for donation in queryset:
            # Check if receipt already exists
            if donation.tax_receipts.exists():
                self.message_user(
                    request,
                    f"Tax receipt already exists for donation {donation.donation_number}.",
                    level=messages.WARNING,
                )
                continue

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
                    donation, tax_receipt, signature_user=request.user
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
                        self.message_user(
                            request,
                            f"Failed to send email for donation {donation.donation_number}: {e}",
                            level=messages.WARNING,
                        )

                generated += 1
            except Exception as e:
                self.message_user(
                    request,
                    f"Failed to generate tax receipt for donation {donation.donation_number}: {e}",
                    level=messages.ERROR,
                )

        if generated > 0:
            self.message_user(
                request,
                f"Successfully generated {generated} tax receipt(s).",
                level=messages.SUCCESS,
            )

    @admin.action(description="Download Tax Receipt (Clean)")
    def download_tax_receipt_clean(self, request, queryset):
        """Download clean tax receipt PDF."""
        if queryset.count() != 1:
            self.message_user(request, "Please select exactly one donation.", level=messages.ERROR)
            return

        donation = queryset.first()
        tax_receipt = donation.tax_receipts.filter(is_copy=False).first()

        if not tax_receipt:
            self.message_user(
                request,
                f"No tax receipt found for donation {donation.donation_number}.",
                level=messages.ERROR,
            )
            return

        try:
            generator = TaxReceiptPDFGenerator(is_copy=False)
            pdf_bytes = generator.generate_pdf(
                donation, tax_receipt, signature_user=tax_receipt.issued_by
            )

            filename = f"tax_receipt_{tax_receipt.serial_number}.pdf"
            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="{filename}"'

            self.message_user(
                request,
                f"Tax receipt downloaded for donation {donation.donation_number}.",
                level=messages.SUCCESS,
            )
            return response

        except Exception as e:
            self.message_user(
                request,
                f"Failed to generate tax receipt: {e}",
                level=messages.ERROR,
            )

    @admin.action(description="Download Tax Receipt (Copy)")
    def download_tax_receipt_copy(self, request, queryset):
        """Download watermarked copy of tax receipt PDF."""
        if queryset.count() != 1:
            self.message_user(request, "Please select exactly one donation.", level=messages.ERROR)
            return

        donation = queryset.first()
        tax_receipt = donation.tax_receipts.filter(is_copy=False).first()

        if not tax_receipt:
            self.message_user(
                request,
                f"No tax receipt found for donation {donation.donation_number}.",
                level=messages.ERROR,
            )
            return

        try:
            generator = TaxReceiptPDFGenerator(is_copy=True)
            pdf_bytes = generator.generate_pdf(
                donation, tax_receipt, signature_user=tax_receipt.issued_by
            )

            filename = f"tax_receipt_{tax_receipt.serial_number}_copy.pdf"
            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="{filename}"'

            self.message_user(
                request,
                f"Tax receipt copy downloaded for donation {donation.donation_number}.",
                level=messages.SUCCESS,
            )
            return response

        except Exception as e:
            self.message_user(
                request,
                f"Failed to generate tax receipt copy: {e}",
                level=messages.ERROR,
            )


@admin.register(TaxReceipt)
class TaxReceiptAdmin(admin.ModelAdmin):
    """Admin interface for TaxReceipt model."""

    list_display = [
        "serial_number",
        "donation",
        "donor_name",
        "issued_date",
        "issued_by",
        "is_copy",
        "created_at",
    ]
    list_filter = ["issued_date", "is_copy"]
    search_fields = [
        "serial_number",
        "donation__donation_number",
        "donation__donor_name",
        "issued_by__username",
    ]
    readonly_fields = ["serial_number", "created_at", "updated_at"]
    actions = ["download_pdf_clean", "download_pdf_copy"]

    fieldsets = (
        (
            "Receipt Information",
            {
                "fields": (
                    "serial_number",
                    "donation",
                    "issued_date",
                    "issued_by",
                    "is_copy",
                )
            },
        ),
        (
            "PDF File",
            {
                "fields": ("pdf_file",),
            },
        ),
        (
            "Metadata",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    @admin.display(description="Donor Name")
    def donor_name(self, obj):
        """Display donor name from donation."""
        return obj.donation.donor_name

    @admin.action(description="Download PDF (Clean)")
    def download_pdf_clean(self, request, queryset):
        """Download clean PDF for selected receipts."""
        if queryset.count() != 1:
            self.message_user(
                request, "Please select exactly one tax receipt.", level=messages.ERROR
            )
            return

        tax_receipt = queryset.first()
        try:
            generator = TaxReceiptPDFGenerator(is_copy=False)
            pdf_bytes = generator.generate_pdf(
                tax_receipt.donation,
                tax_receipt,
                signature_user=tax_receipt.issued_by,
            )

            filename = f"tax_receipt_{tax_receipt.serial_number}.pdf"
            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="{filename}"'

            self.message_user(
                request,
                "Tax receipt downloaded.",
                level=messages.SUCCESS,
            )
            return response

        except Exception as e:
            self.message_user(
                request,
                f"Failed to generate tax receipt: {e}",
                level=messages.ERROR,
            )

    @admin.action(description="Download PDF (Copy)")
    def download_pdf_copy(self, request, queryset):
        """Download watermarked copy PDF for selected receipts."""
        if queryset.count() != 1:
            self.message_user(
                request, "Please select exactly one tax receipt.", level=messages.ERROR
            )
            return

        tax_receipt = queryset.first()
        try:
            generator = TaxReceiptPDFGenerator(is_copy=True)
            pdf_bytes = generator.generate_pdf(
                tax_receipt.donation,
                tax_receipt,
                signature_user=tax_receipt.issued_by,
            )

            filename = f"tax_receipt_{tax_receipt.serial_number}_copy.pdf"
            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="{filename}"'

            self.message_user(
                request,
                "Tax receipt copy downloaded.",
                level=messages.SUCCESS,
            )
            return response

        except Exception as e:
            self.message_user(
                request,
                f"Failed to generate tax receipt copy: {e}",
                level=messages.ERROR,
            )


@admin.register(DonationItem)
class DonationItemAdmin(admin.ModelAdmin):
    """Admin interface for DonationItem model."""

    list_display = [
        "name",
        "donation",
        "quantity",
        "condition",
        "status",
        "access_code",
        "asset",
        "inventory_item",
        "remaining_quantity",
    ]
    list_filter = ["status", "condition", "donation__status"]
    search_fields = [
        "name",
        "description",
        "donation__donor_name",
        "donation__donation_number",
    ]
    readonly_fields = ["created_at", "updated_at", "remaining_quantity"]
    fieldsets = (
        (
            "Item Information",
            {
                "fields": (
                    "donation",
                    "name",
                    "description",
                    "quantity",
                )
            },
        ),
        (
            "Status and Condition",
            {
                "fields": (
                    "condition",
                    "status",
                    "notes",
                )
            },
        ),
        (
            "QR Code",
            {
                "fields": (
                    "access_code",
                    "qr_code",
                ),
                "description": "QR code for tracking this item",
            },
        ),
        (
            "Links to Assets/Inventory",
            {
                "fields": (
                    "asset",
                    "inventory_item",
                ),
                "description": "Optional links when item becomes a tracked asset or inventory item",
            },
        ),
        (
            "Metadata",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                    "remaining_quantity",
                )
            },
        ),
    )


@admin.register(Disposition)
class DispositionAdmin(admin.ModelAdmin):
    """Admin interface for Disposition model."""

    list_display = [
        "donation_item",
        "disposition_type",
        "quantity",
        "disposition_date",
        "disposed_by",
        "sale_method",
        "sale_price",
        "kept_destination",
        "kept_for_sig",
        "created_asset",
    ]
    list_filter = ["disposition_type", "disposition_date"]
    search_fields = [
        "donation_item__name",
        "donation_item__donation__donor_name",
        "recipient_name",
        "notes",
    ]
    readonly_fields = ["created_at", "updated_at"]
    fieldsets = (
        (
            "Disposition Information",
            {
                "fields": (
                    "donation_item",
                    "disposition_type",
                    "quantity",
                    "disposition_date",
                    "disposed_by",
                )
            },
        ),
        (
            "Sale Information",
            {
                "fields": (
                    "sale_method",
                    "sale_price",
                    "recipient_name",
                ),
                "description": "For sold or auctioned items",
            },
        ),
        (
            "Kept Item Information",
            {
                "fields": (
                    "kept_destination",
                    "kept_for_sig",
                ),
                "description": "For kept items - specify if for makerspace or a SIG",
            },
        ),
        (
            "Details",
            {
                "fields": ("notes",),
            },
        ),
        (
            "Asset Creation",
            {
                "fields": ("created_asset",),
                "description": "Link to Asset if this disposition created one",
            },
        ),
        (
            "Metadata",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )
