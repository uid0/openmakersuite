"""API views for the vendors app."""

from rest_framework import viewsets
from rest_framework.permissions import IsAdminUser, IsAuthenticated

from .models import Vendor
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
