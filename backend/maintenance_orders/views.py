"""API views for the third-party maintenance work order app."""

from rest_framework import viewsets
from rest_framework.permissions import IsAdminUser, IsAuthenticated

from .models import ThirdPartyWorkOrder, ThirdPartyWorkOrderAsset, ThirdPartyWorkOrderAttachment
from .serializers import (
    ThirdPartyWorkOrderAssetSerializer,
    ThirdPartyWorkOrderAttachmentSerializer,
    ThirdPartyWorkOrderSerializer,
)


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
