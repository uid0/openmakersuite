"""Custom middleware for the OMS backend."""


class PermissionsPolicyMiddleware:
    """Set a Permissions-Policy header allowing camera/microphone access.

    Modern browsers (Chrome 94+, Safari 16+) require an explicit
    Permissions-Policy header to enable getUserMedia in many embedded or
    cross-origin contexts. The QR-code checklist scanner needs camera access
    on every page that loads it, so the simplest correct policy is to allow
    camera site-wide and disallow microphone (we never use it).
    """

    HEADER_VALUE = "camera=*, microphone=()"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Permissions-Policy", self.HEADER_VALUE)
        return response
