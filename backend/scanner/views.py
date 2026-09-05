"""Universal scanner dispatch endpoint.

`POST /api/scanner/dispatch/ {payload: "..."}` resolves the scanned
string against the OMS data model and returns what action the client
should take next. The endpoint never mutates state — side effects
(reorder requests, stock receives, location/asset check-ins) happen via
the existing per-entity endpoints once the user confirms.

`AllowAny` so kiosk-mode shared phones can scan without a JWT. The
endpoint emits no user-attributed audit rows on its own, and the
follow-up side-effect endpoints enforce their own auth.

Throttled per-IP at the ``scan_dispatch`` scope (60/min) — a busy
kiosk fires many scans per minute, but no single IP should hammer it.
See ``REST_FRAMEWORK.DEFAULT_THROTTLE_RATES`` for the rate.
"""

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from inventory.services.vendor_visibility import may_see_vendor_data

from .resolvers import resolve
from .serializers import ScanDispatchRequestSerializer


class _ScanDispatchThrottle(ScopedRateThrottle):
    """ScopedRateThrottle baked with a fixed scope.

    ScopedRateThrottle.allow_request normally reads ``view.throttle_scope``
    and returns True (no throttling) if the view doesn't have one.
    Function-based @api_view doesn't carry that attribute, so we override
    allow_request to use this subclass's ``scope`` class attribute
    directly. The rate is still looked up from
    ``DEFAULT_THROTTLE_RATES[scope]`` so all rate config stays in
    settings.py.
    """

    scope = "scan_dispatch"

    def allow_request(self, request, view):
        # Bake the rate from the class-level scope, then delegate to
        # SimpleRateThrottle (the grandparent), skipping
        # ScopedRateThrottle's view.throttle_scope lookup.
        self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)
        return super(ScopedRateThrottle, self).allow_request(request, view)


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([_ScanDispatchThrottle])
def dispatch_scan(request):
    request_serializer = ScanDispatchRequestSerializer(data=request.data)
    request_serializer.is_valid(raise_exception=True)
    payload = request_serializer.validated_data["payload"]

    result = resolve(payload)
    # A UPC scan resolves through ``ItemSupplier``, so the result carries the
    # vendor's name. This endpoint is ``AllowAny`` and stays that way — it is
    # the barcode gun's entry point — so the gate is on the two vendor keys
    # (op-anonymous-read-posture). Everything the scan is FOR (which item, what
    # action, current stock) reaches every caller.
    return Response(
        result.to_dict(include_vendor_data=may_see_vendor_data(request)),
        status=status.HTTP_200_OK,
    )
