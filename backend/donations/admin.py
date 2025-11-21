"""
Admin interface for donations app.
"""

from django.contrib import admin, messages
from django.http import HttpResponse
from django.shortcuts import redirect

from .models import Disposition, Donation, DonationItem


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
    readonly_fields = ["donation_number", "created_at", "updated_at"]
    actions = ["generate_items_from_estimate", "download_label_sheet"]
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
    search_fields = ["name", "description", "donation__donor_name", "donation__donation_number"]
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
