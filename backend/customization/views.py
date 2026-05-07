"""
Views for customization API.
"""

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .audit import (
    diff_audited_fields,
)
from .audit import record_event as record_settings_audit_event
from .audit import (
    snapshot,
)
from .models import SiteSettings, SiteSettingsAuditEvent
from .serializers import SiteSettingsSerializer, SiteSettingsUpdateSerializer


@api_view(["GET", "PUT", "PATCH"])
@permission_classes([AllowAny])
def site_settings(request):
    """
    Get or update the current site settings.

    GET: Public endpoint (AllowAny) - returns current site settings
    PUT/PATCH: Requires superuser authentication - updates site settings
    """
    if request.method == "GET":
        settings_obj = SiteSettings.get()
        serializer = SiteSettingsSerializer(settings_obj, context={"request": request})
        return Response(serializer.data)

    # PUT/PATCH - requires authentication and superuser
    if not request.user.is_authenticated:
        return Response(
            {"detail": "Authentication required."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    if not request.user.is_superuser:
        return Response(
            {"detail": "You do not have permission to update site settings."},
            status=status.HTTP_403_FORBIDDEN,
        )

    settings_obj = SiteSettings.get()

    # Handle logo deletion - check if logo field is an empty string in request.data
    # (FormData sends empty string for file deletion)
    data = request.data.copy()
    if "logo" in data and data["logo"] == "":
        # Delete the existing logo file if it exists
        if settings_obj.logo:
            settings_obj.logo.delete(save=False)
        data["logo"] = None

    serializer = SiteSettingsUpdateSerializer(
        settings_obj, data=data, partial=True, context={"request": request}
    )

    if serializer.is_valid():
        before = snapshot(settings_obj)
        serializer.save()
        # Refresh to ensure file fields reflect post-save state when the
        # storage backend renames on upload.
        settings_obj.refresh_from_db()
        changes = diff_audited_fields(before, settings_obj)
        if changes:
            record_settings_audit_event(
                action=SiteSettingsAuditEvent.ACTION_SETTINGS_UPDATE,
                actor=request.user,
                site_settings=settings_obj,
                metadata={"changes": changes},
            )
        # Return updated settings using the read serializer
        read_serializer = SiteSettingsSerializer(settings_obj, context={"request": request})
        return Response(read_serializer.data, status=status.HTTP_200_OK)

    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
