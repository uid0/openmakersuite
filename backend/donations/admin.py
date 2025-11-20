"""
Admin interface for donations app.
"""

from django.contrib import admin

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
