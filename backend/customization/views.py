"""
Views for customization API.
"""

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import SiteSettings
from .serializers import SiteSettingsSerializer


@api_view(["GET"])
@permission_classes([AllowAny])
def site_settings(request):
    """
    Get the current site settings.

    This endpoint is public (AllowAny) so the frontend can access
    customization settings without authentication.
    """
    settings_obj = SiteSettings.get()
    serializer = SiteSettingsSerializer(settings_obj, context={"request": request})
    return Response(serializer.data)
