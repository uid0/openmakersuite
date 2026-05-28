"""Custom middleware for the OMS backend."""

import sentry_sdk


def _role_for(user) -> str:
    """Map a Django user to a coarse role label used as a Sentry tag.

    Coarse because filtering by `is_staff=True` on its own already
    captures most operational queries, and adding finer-grained group
    membership (SIG leads, board, …) would explode the tag cardinality
    in Sentry without much extra signal. If a future investigation
    needs that, add a dedicated tag (e.g. `sig_role`) rather than
    splintering this one.
    """
    if not getattr(user, "is_authenticated", False):
        return "anonymous"
    if getattr(user, "is_superuser", False):
        return "admin"
    if getattr(user, "is_staff", False):
        return "staff"
    return "member"


class SentryUserScopeMiddleware:
    """Attach Sentry user + role context to every request.

    Sentry's "users affected" counts and the user filter on the issue
    detail page are populated from `sentry_sdk.set_user(...)`. Without
    this middleware every event lands anonymously and uid0 can't slice
    incidents by who hit them. Pairs with the `oms.user_role` tag so
    common dashboards can split anonymous public-scan traffic from
    authenticated member traffic.

    Sentry SDK 2.x clears the user/tag scope at the end of each
    request automatically, so no teardown is needed here.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        role = _role_for(user)
        sentry_sdk.set_tag("oms.user_role", role)
        if role != "anonymous" and user is not None:
            sentry_sdk.set_user(
                {
                    "id": user.id,
                    "username": user.username,
                    "ip_address": "{{auto}}",
                }
            )
        return self.get_response(request)


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
