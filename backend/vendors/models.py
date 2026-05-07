"""
Third-party vendor models.

Vendors here are external service providers (HVAC, electrical, plumbing, etc.)
who perform maintenance work on assets. They are intentionally separate from
inventory.Supplier (which represents parts retailers) — the two domains rarely
overlap, and conflating them would muddy compliance/insurance fields that only
apply to service vendors (TDLR licensing, COI tracking).
"""

import uuid

from django.conf import settings
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


class VendorAuditEvent(models.Model):
    """Append-only audit log for vendor compliance and lifecycle actions.

    Captures actor, action, vendor reference, notes, and per-action metadata
    (incl. before/after for compliance-field changes so reviewers can answer
    'who changed the COI date and to what?'). Rows are written by
    ``vendors.audit.record_event`` and never updated or deleted by
    application code.

    Per gh #356 / #334. Mirrors the per-domain audit tables in #352-#355.
    """

    ACTION_VENDOR_CREATE = "vendor_create"
    ACTION_VENDOR_DEACTIVATE = "vendor_deactivate"
    ACTION_VENDOR_REACTIVATE = "vendor_reactivate"
    ACTION_COMPLIANCE_UPDATE = "compliance_update"

    ACTION_CHOICES = [
        (ACTION_VENDOR_CREATE, "Vendor created"),
        (ACTION_VENDOR_DEACTIVATE, "Vendor deactivated"),
        (ACTION_VENDOR_REACTIVATE, "Vendor reactivated"),
        (ACTION_COMPLIANCE_UPDATE, "Compliance fields updated"),
    ]

    # Compliance fields the audit hook tracks for diff capture.
    COMPLIANCE_FIELDS = (
        "tdlr_license_number",
        "tdlr_license_expires_at",
        "coi_provider",
        "coi_policy_number",
        "coi_expires_at",
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vendor_audit_actions",
        help_text="User who performed the action; null for system-initiated events.",
    )
    action = models.CharField(max_length=32, choices=ACTION_CHOICES)
    vendor = models.ForeignKey(
        Vendor,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    notes = models.TextField(blank=True)
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Action-specific payload. For compliance_update, includes a "
            "'changes' dict mapping field name -> {before, after}."
        ),
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["vendor", "-created_at"], name="vendor_audit_vendor_idx"),
            models.Index(fields=["actor", "-created_at"], name="vendor_audit_actor_idx"),
            models.Index(fields=["action", "-created_at"], name="vendor_audit_action_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.action} ({self.vendor_id}) @ {self.created_at:%Y-%m-%d %H:%M}"
