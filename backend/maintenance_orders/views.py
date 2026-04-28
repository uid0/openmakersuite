"""API views for the third-party maintenance work order app."""

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from inventory.models import Asset
from vendors.models import Vendor

from . import transitions as wo_transitions
from .models import (
    AssetWarranty,
    EmergencyAuthorization,
    ThirdPartyWorkOrder,
    ThirdPartyWorkOrderAsset,
    ThirdPartyWorkOrderAttachment,
    ThirdPartyWorkOrderQuote,
)
from .serializers import (
    AssetWarrantySerializer,
    EmergencyAuthorizationSerializer,
    ThirdPartyWorkOrderAssetSerializer,
    ThirdPartyWorkOrderAttachmentSerializer,
    ThirdPartyWorkOrderQuoteSerializer,
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


def _decimal_or_none(value):
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError(f"Invalid decimal value: {value!r}") from exc


class ThirdPartyWorkOrderViewSet(_StaffWriteMixin, viewsets.ModelViewSet):
    queryset = ThirdPartyWorkOrder.objects.select_related("vendor", "asset").prefetch_related(
        "asset_links__asset", "attachments", "quotes__vendor"
    )
    serializer_class = ThirdPartyWorkOrderSerializer
    filterset_fields = ["status", "work_type", "is_emergency", "vendor", "asset"]

    def perform_create(self, serializer):
        if self.request.user and self.request.user.is_authenticated:
            serializer.save(opened_by=self.request.user)
        else:
            serializer.save()

    # ---- Step 1: NTE / emergency authorization -------------------------

    @action(detail=True, methods=["post"], url_path="set-nte")
    def set_nte(self, request, pk=None):
        wo = self.get_object()
        amount = _decimal_or_none(request.data.get("nte_amount"))
        if amount is None:
            return Response({"detail": "nte_amount is required."}, status=400)
        try:
            wo_transitions.set_nte(wo, user=request.user, nte_amount=amount)
        except (PermissionDenied, ValidationError) as exc:
            return _err(exc)
        return Response(self.get_serializer(wo).data)

    @action(detail=True, methods=["post"], url_path="authorize-emergency")
    def authorize_emergency(self, request, pk=None):
        wo = self.get_object()
        try:
            auth = wo_transitions.authorize_emergency(
                wo, user=request.user, reason=request.data.get("reason", "")
            )
        except (PermissionDenied, ValidationError) as exc:
            return _err(exc)
        return Response(EmergencyAuthorizationSerializer(auth).data, status=201)

    # ---- Step 2: sourcing / quotes / waiver ----------------------------

    @action(detail=True, methods=["post"], url_path="advance-to-sourcing")
    def advance_to_sourcing(self, request, pk=None):
        wo = self.get_object()
        try:
            wo_transitions.advance_to_sourcing(wo, user=request.user)
        except (PermissionDenied, ValidationError) as exc:
            return _err(exc)
        return Response(self.get_serializer(wo).data)

    @action(detail=True, methods=["post"], url_path="waive-quote-requirement")
    def waive_quote_requirement(self, request, pk=None):
        wo = self.get_object()
        reason = request.data.get("reason", "")
        try:
            wo_transitions.waive_quote_requirement(wo, user=request.user, reason=reason)
        except (PermissionDenied, ValidationError) as exc:
            return _err(exc)
        return Response(self.get_serializer(wo).data)

    @action(detail=True, methods=["post"], url_path="advance-to-scheduled")
    def advance_to_scheduled(self, request, pk=None):
        wo = self.get_object()
        try:
            wo_transitions.advance_to_scheduled(wo, user=request.user)
        except (PermissionDenied, ValidationError) as exc:
            return _err(exc)
        return Response(self.get_serializer(wo).data)

    # ---- Step 3 / 4: arrival + sign-off --------------------------------

    @action(detail=True, methods=["post"], url_path="vendor-arrived")
    def vendor_arrived(self, request, pk=None):
        wo = self.get_object()
        shadow_user = None
        shadow_user_id = request.data.get("shadow_user")
        if shadow_user_id:
            from django.contrib.auth import get_user_model

            User = get_user_model()
            try:
                shadow_user = User.objects.get(pk=shadow_user_id)
            except User.DoesNotExist:
                return Response({"detail": "shadow_user not found"}, status=400)
        try:
            wo_transitions.vendor_arrived(
                wo,
                user=request.user,
                keyfob_id=request.data.get("keyfob_id", ""),
                shadow_user=shadow_user,
            )
        except (PermissionDenied, ValidationError) as exc:
            return _err(exc)
        return Response(self.get_serializer(wo).data)

    @action(detail=True, methods=["post"], url_path="sign-off")
    def sign_off(self, request, pk=None):
        wo = self.get_object()
        try:
            wo_transitions.ops_sign_off(wo, user=request.user)
        except (PermissionDenied, ValidationError) as exc:
            return _err(exc)
        return Response(self.get_serializer(wo).data)

    # ---- Step 6 / 7: financial review + closure ------------------------

    @action(detail=True, methods=["post"], url_path="advance-to-financial-review")
    def advance_to_financial_review(self, request, pk=None):
        wo = self.get_object()
        try:
            invoice = _decimal_or_none(request.data.get("actual_invoice_total"))
            dispatch = _decimal_or_none(request.data.get("dispatch_fee"))
            wo_transitions.advance_to_financial_review(
                wo,
                user=request.user,
                actual_invoice_total=invoice,
                dispatch_fee=dispatch,
            )
        except (PermissionDenied, ValidationError) as exc:
            return _err(exc)
        return Response(self.get_serializer(wo).data)

    @action(detail=True, methods=["post"], url_path="close")
    def close(self, request, pk=None):
        wo = self.get_object()
        try:
            wo_transitions.close_work_order(wo, user=request.user)
        except (PermissionDenied, ValidationError) as exc:
            return _err(exc)
        return Response(self.get_serializer(wo).data)


def _err(exc):
    if isinstance(exc, PermissionDenied):
        return Response({"detail": str(exc)}, status=403)
    detail = exc.messages if hasattr(exc, "messages") else [str(exc)]
    return Response({"detail": detail[0] if len(detail) == 1 else detail}, status=400)


class ThirdPartyWorkOrderAssetViewSet(_StaffWriteMixin, viewsets.ModelViewSet):
    queryset = ThirdPartyWorkOrderAsset.objects.select_related("work_order", "asset")
    serializer_class = ThirdPartyWorkOrderAssetSerializer
    filterset_fields = ["work_order", "asset"]


class ThirdPartyWorkOrderQuoteViewSet(_StaffWriteMixin, viewsets.ModelViewSet):
    queryset = ThirdPartyWorkOrderQuote.objects.select_related("work_order", "vendor")
    serializer_class = ThirdPartyWorkOrderQuoteSerializer
    filterset_fields = ["work_order", "vendor"]

    def perform_create(self, serializer):
        if self.request.user and self.request.user.is_authenticated:
            serializer.save(submitted_by=self.request.user)
        else:
            serializer.save()


class EmergencyAuthorizationViewSet(_StaffWriteMixin, viewsets.ReadOnlyModelViewSet):
    queryset = EmergencyAuthorization.objects.select_related("work_order", "authorized_by")
    serializer_class = EmergencyAuthorizationSerializer
    filterset_fields = ["work_order", "authorized_by"]


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
