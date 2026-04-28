"""API views for the third-party maintenance work order app."""

from datetime import timedelta

from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import status, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from inventory.models import Asset
from vendors.models import Vendor

from .models import (
    AssetWarranty,
    ThirdPartyWorkOrder,
    ThirdPartyWorkOrderAsset,
    ThirdPartyWorkOrderAttachment,
)
from .serializers import (
    AssetWarrantySerializer,
    ThirdPartyWorkOrderAssetSerializer,
    ThirdPartyWorkOrderAttachmentSerializer,
    ThirdPartyWorkOrderSerializer,
)
from .signals import active_warranty_for


class _StaffWriteMixin:
    """Read for any authenticated user, write for staff only.

    Phase 2 will refine this to SIG-admin scoping by the asset's owning_group.
    Until then we use a coarse staff gate so Ops can read but only admins
    create/update.
    """

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return [IsAdminUser()]


class ThirdPartyWorkOrderViewSet(_StaffWriteMixin, viewsets.ModelViewSet):
    queryset = ThirdPartyWorkOrder.objects.select_related("vendor", "asset").prefetch_related(
        "asset_links__asset", "attachments"
    )
    serializer_class = ThirdPartyWorkOrderSerializer
    filterset_fields = ["status", "work_type", "is_emergency", "vendor", "asset"]

    def perform_create(self, serializer):
        if self.request.user and self.request.user.is_authenticated:
            serializer.save(opened_by=self.request.user)
        else:
            serializer.save()


class ThirdPartyWorkOrderAssetViewSet(_StaffWriteMixin, viewsets.ModelViewSet):
    queryset = ThirdPartyWorkOrderAsset.objects.select_related("work_order", "asset")
    serializer_class = ThirdPartyWorkOrderAssetSerializer
    filterset_fields = ["work_order", "asset"]


class ThirdPartyWorkOrderAttachmentViewSet(_StaffWriteMixin, viewsets.ModelViewSet):
    queryset = ThirdPartyWorkOrderAttachment.objects.select_related("work_order", "uploaded_by")
    serializer_class = ThirdPartyWorkOrderAttachmentSerializer
    filterset_fields = ["work_order", "kind"]

    def perform_create(self, serializer):
        if self.request.user and self.request.user.is_authenticated:
            serializer.save(uploaded_by=self.request.user)
        else:
            serializer.save()


class AssetWarrantyViewSet(_StaffWriteMixin, viewsets.ModelViewSet):
    queryset = AssetWarranty.objects.select_related("asset")
    serializer_class = AssetWarrantySerializer
    filterset_fields = ["asset", "provider"]


def _vendor_compliance_payload(vendor: Vendor) -> dict:
    today = timezone.now().date()
    soon = today + timedelta(days=30)

    def _bucket(date_value):
        if date_value is None:
            return "missing"
        if date_value < today:
            return "expired"
        if date_value <= soon:
            return "expiring_soon"
        return "ok"

    return {
        "vendor_id": str(vendor.id),
        "vendor_name": vendor.name,
        "is_active": vendor.is_active,
        "tdlr_status": _bucket(vendor.tdlr_license_expires_at),
        "tdlr_expires_at": vendor.tdlr_license_expires_at,
        "coi_status": _bucket(vendor.coi_expires_at),
        "coi_expires_at": vendor.coi_expires_at,
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def asset_wo_status(request, asset_id):
    """Pre-flight status for opening a WO against an asset.

    Returns the asset's active warranty (if any) plus, when ``vendor`` query
    param is supplied, that vendor's TDLR/COI compliance bucketing. Drives
    the frontend's inline warning banners on the WO creation form.
    """
    asset = get_object_or_404(Asset, pk=asset_id)
    warranty = active_warranty_for(asset)
    payload = {
        "asset_id": str(asset.id),
        "warranty": AssetWarrantySerializer(warranty).data if warranty else None,
        "warranty_recovery_recommended": warranty is not None,
    }
    vendor_id = request.query_params.get("vendor")
    if vendor_id:
        try:
            vendor = Vendor.objects.get(pk=vendor_id)
        except Vendor.DoesNotExist:
            return Response({"detail": "vendor not found"}, status=status.HTTP_404_NOT_FOUND)
        payload["vendor_compliance"] = _vendor_compliance_payload(vendor)
    return Response(payload)
