"""Universal scanner dispatch endpoint.

`POST /api/scanner/dispatch/ {payload: "..."}` resolves the scanned
string against the OMS data model and returns what action the client
should take next. The endpoint never mutates state — side effects
(reorder requests, stock receives, location/asset check-ins) happen via
the existing per-entity endpoints once the user confirms.

`AllowAny` so kiosk-mode shared phones can scan without a JWT. The
endpoint emits no user-attributed audit rows on its own, and the
follow-up side-effect endpoints enforce their own auth.
"""

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .resolvers import resolve
from .serializers import ScanDispatchRequestSerializer


@api_view(["POST"])
@permission_classes([AllowAny])
def dispatch_scan(request):
    request_serializer = ScanDispatchRequestSerializer(data=request.data)
    request_serializer.is_valid(raise_exception=True)
    payload = request_serializer.validated_data["payload"]

    result = resolve(payload)
    return Response(result.to_dict(), status=status.HTTP_200_OK)
