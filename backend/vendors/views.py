"""API views for the vendors app."""

from rest_framework import viewsets
from rest_framework.permissions import IsAdminUser, IsAuthenticated

from .audit import diff_compliance_fields
from .audit import record_event as record_vendor_audit_event
from .models import Vendor, VendorAuditEvent
from .serializers import VendorSerializer


class VendorViewSet(viewsets.ModelViewSet):
    """CRUD API for third-party service vendors.

    Read access is granted to any authenticated user (Ops needs to see vendor
    contact info to schedule work). Write access is restricted to staff —
    Phase 2 will refine this once SIG-admin scoping lands.
    """

    queryset = Vendor.objects.all()
    serializer_class = VendorSerializer
    filterset_fields = ["vendor_kind", "is_active"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return [IsAdminUser()]

    def get_queryset(self):
        qs = super().get_queryset()
        only_active = self.request.query_params.get("active_only")
        if only_active and only_active.lower() in ("1", "true", "yes"):
            qs = qs.filter(is_active=True)
        return qs

    def perform_create(self, serializer):
        vendor = serializer.save()
        record_vendor_audit_event(
            action=VendorAuditEvent.ACTION_VENDOR_CREATE,
            actor=self.request.user,
            vendor=vendor,
            metadata={
                "name": vendor.name,
                "vendor_kind": vendor.vendor_kind,
            },
        )

    def perform_update(self, serializer):
        # Snapshot the pre-save instance so we can diff compliance fields and
        # detect is_active toggles. ``serializer.instance`` is the loaded row;
        # we reload it cheaply by deep-copying the relevant attrs.
        before_compliance = Vendor(
            **{
                name: getattr(serializer.instance, name)
                for name in VendorAuditEvent.COMPLIANCE_FIELDS
            }
        )
        before_is_active = serializer.instance.is_active

        vendor = serializer.save()

        changes = diff_compliance_fields(before_compliance, vendor)
        if changes:
            record_vendor_audit_event(
                action=VendorAuditEvent.ACTION_COMPLIANCE_UPDATE,
                actor=self.request.user,
                vendor=vendor,
                metadata={"changes": changes},
            )

        if before_is_active != vendor.is_active:
            record_vendor_audit_event(
                action=(
                    VendorAuditEvent.ACTION_VENDOR_REACTIVATE
                    if vendor.is_active
                    else VendorAuditEvent.ACTION_VENDOR_DEACTIVATE
                ),
                actor=self.request.user,
                vendor=vendor,
            )
