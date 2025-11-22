"""
Serializers for donations app.
"""

from rest_framework import serializers

from .models import Disposition, Donation, DonationItem, TaxReceipt


class DonationItemSerializer(serializers.ModelSerializer):
    """Serializer for DonationItem."""

    donation_number = serializers.CharField(source="donation.donation_number", read_only=True)
    donor_name = serializers.CharField(source="donation.donor_name", read_only=True)

    class Meta:
        model = DonationItem
        fields = [
            "id",
            "donation",
            "donation_number",
            "donor_name",
            "name",
            "description",
            "quantity",
            "condition",
            "status",
            "access_code",
            "qr_code",
            "asset",
            "inventory_item",
            "notes",
            "remaining_quantity",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "access_code",
            "qr_code",
            "remaining_quantity",
            "created_at",
            "updated_at",
        ]


class DispositionSerializer(serializers.ModelSerializer):
    """Serializer for Disposition."""

    donation_item_name = serializers.CharField(source="donation_item.name", read_only=True)
    donation_number = serializers.CharField(
        source="donation_item.donation.donation_number", read_only=True
    )

    class Meta:
        model = Disposition
        fields = [
            "id",
            "donation_item",
            "donation_item_name",
            "donation_number",
            "disposition_type",
            "quantity",
            "disposition_date",
            "disposed_by",
            "sale_method",
            "sale_price",
            "kept_destination",
            "kept_for_sig",
            "notes",
            "recipient_name",
            "created_asset",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class DonationSerializer(serializers.ModelSerializer):
    """Serializer for Donation."""

    items = DonationItemSerializer(many=True, read_only=True)
    total_items = serializers.IntegerField(read_only=True)
    total_quantity = serializers.IntegerField(read_only=True)
    net_value = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = Donation
        fields = [
            "id",
            "donation_number",
            "donor_name",
            "donor_email",
            "donor_phone",
            "donor_address",
            "date_received",
            "received_by",
            "received_notes",
            "status",
            "reviewed_by",
            "reviewed_at",
            "review_notes",
            "estimated_number_of_items",
            "estimated_value",
            "associated_costs",
            "cost_notes",
            "net_value",
            "tax_receipt_issued",
            "tax_receipt_number",
            "tax_receipt_issued_at",
            "items",
            "total_items",
            "total_quantity",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "donation_number",
            "total_items",
            "total_quantity",
            "net_value",
            "created_at",
            "updated_at",
        ]


class DonationListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for donation lists."""

    total_items = serializers.IntegerField(read_only=True)
    total_quantity = serializers.IntegerField(read_only=True)
    net_value = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = Donation
        fields = [
            "id",
            "donation_number",
            "donor_name",
            "date_received",
            "status",
            "estimated_number_of_items",
            "estimated_value",
            "associated_costs",
            "net_value",
            "total_items",
            "total_quantity",
            "created_at",
        ]


class TaxReceiptSerializer(serializers.ModelSerializer):
    """Serializer for TaxReceipt."""

    donation_number = serializers.CharField(source="donation.donation_number", read_only=True)
    donor_name = serializers.CharField(source="donation.donor_name", read_only=True)
    donor_email = serializers.EmailField(source="donation.donor_email", read_only=True)
    issued_by_username = serializers.CharField(source="issued_by.username", read_only=True)

    class Meta:
        model = TaxReceipt
        fields = [
            "id",
            "serial_number",
            "donation",
            "donation_number",
            "donor_name",
            "donor_email",
            "issued_date",
            "issued_by",
            "issued_by_username",
            "pdf_file",
            "is_copy",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "serial_number",
            "created_at",
            "updated_at",
        ]
