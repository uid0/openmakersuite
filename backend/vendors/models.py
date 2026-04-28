"""
Third-party vendor models.

Vendors here are external service providers (HVAC, electrical, plumbing, etc.)
who perform maintenance work on assets. They are intentionally separate from
inventory.Supplier (which represents parts retailers) — the two domains rarely
overlap, and conflating them would muddy compliance/insurance fields that only
apply to service vendors (TDLR licensing, COI tracking).
"""

import uuid

from django.db import models


class Vendor(models.Model):
    """A third-party service vendor performing maintenance on assets."""

    KIND_HVAC = "hvac"
    KIND_ELECTRICAL = "electrical"
    KIND_PLUMBING = "plumbing"
    KIND_GENERAL = "general"
    KIND_LANDSCAPING = "landscaping"
    KIND_SECURITY = "security"
    KIND_PEST_CONTROL = "pest_control"
    KIND_FIRE = "fire_safety"
    KIND_ROOFING = "roofing"
    KIND_OTHER = "other"

    KIND_CHOICES = [
        (KIND_HVAC, "HVAC"),
        (KIND_ELECTRICAL, "Electrical"),
        (KIND_PLUMBING, "Plumbing"),
        (KIND_GENERAL, "General Contractor"),
        (KIND_LANDSCAPING, "Landscaping"),
        (KIND_SECURITY, "Security"),
        (KIND_PEST_CONTROL, "Pest Control"),
        (KIND_FIRE, "Fire Safety"),
        (KIND_ROOFING, "Roofing"),
        (KIND_OTHER, "Other"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, help_text="Vendor business name")
    vendor_kind = models.CharField(
        max_length=32,
        choices=KIND_CHOICES,
        default=KIND_OTHER,
        help_text="Service category — used to map TDLR license requirements to asset categories",
    )

    contact_name = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)
    address = models.TextField(blank=True)

    tdlr_license_number = models.CharField(
        max_length=64,
        blank=True,
        help_text="Texas Department of Licensing & Regulation license number, if required for this vendor kind",
    )
    tdlr_license_expires_at = models.DateField(
        null=True,
        blank=True,
        help_text="Expiration date of the TDLR license — sourcing should block expired vendors",
    )

    coi_provider = models.CharField(
        max_length=200, blank=True, help_text="Certificate of Insurance carrier name"
    )
    coi_policy_number = models.CharField(max_length=128, blank=True)
    coi_expires_at = models.DateField(
        null=True,
        blank=True,
        help_text="Expiration date of the certificate of insurance",
    )

    notes = models.TextField(blank=True)
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive vendors are hidden from sourcing UI but retained for historical work orders",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["vendor_kind", "is_active"], name="vendor_kind_active_idx"),
            models.Index(fields=["is_active", "name"], name="vendor_active_name_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_vendor_kind_display()})"

    @property
    def tdlr_is_expired(self) -> bool:
        """True if a TDLR license is on file and its expiration date has passed."""
        from django.utils import timezone

        if not self.tdlr_license_expires_at:
            return False
        return timezone.now().date() > self.tdlr_license_expires_at

    @property
    def coi_is_expired(self) -> bool:
        """True if a COI is on file and its expiration date has passed."""
        from django.utils import timezone

        if not self.coi_expires_at:
            return False
        return timezone.now().date() > self.coi_expires_at
