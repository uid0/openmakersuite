"""AC-7: API error responses are consistent.

Each test maps to a row of the documented error contract
(``docs/API_ERROR_CONTRACT.md``). For every exception class DRF raises in a
real workflow, the response must use the standardized envelope::

    {"error": {"code": "...", "message": "...", "details": ...}}
"""

from __future__ import annotations

from django.urls import include, path

import pytest
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.routers import SimpleRouter
from rest_framework.test import APIClient, URLPatternsTestCase
from rest_framework.throttling import SimpleRateThrottle

from config.api_errors import Conflict, DependencyUnavailable, ErrorCode, TaskQueued, error_response


class _OncePerMinuteThrottle(SimpleRateThrottle):
    """Throttle keyed to a fixed value so the second call in a test trips it.
    Avoids real-time sleeps."""

    rate = "1/min"
    scope = "test"

    def get_cache_key(self, request, view):
        return "test-throttle-key"


class _ContractViewSet(viewsets.ViewSet):
    permission_classes: list = []

    @action(detail=False, methods=["post"], url_path="validate")
    def validate_endpoint(self, request):
        class _S(serializers.Serializer):
            email = serializers.EmailField()

        s = _S(data=request.data)
        s.is_valid(raise_exception=True)
        return Response(s.validated_data)

    @action(detail=False, methods=["get"], url_path="forbidden")
    def forbidden(self, request):
        raise PermissionDenied("You cannot do that.")

    @action(
        detail=False,
        methods=["get"],
        url_path="needs-auth",
        permission_classes=[IsAuthenticated],
    )
    def needs_auth(self, request):
        return Response({"ok": True})

    @action(detail=False, methods=["get"], url_path="missing")
    def missing(self, request):
        raise NotFound()

    @action(detail=False, methods=["get"], url_path="dep-down")
    def dep_down(self, request):
        raise DependencyUnavailable(detail={"dependency": "redis"})

    @action(detail=False, methods=["post"], url_path="async-task")
    def async_task(self, request):
        raise TaskQueued(detail={"task_id": "abc-123"})

    @action(detail=False, methods=["post"], url_path="conflict")
    def conflict_endpoint(self, request):
        raise Conflict("Resource is locked.")

    @action(
        detail=False,
        methods=["get"],
        url_path="throttled",
        throttle_classes=[_OncePerMinuteThrottle],
    )
    def rate_limited(self, request):
        return Response({"ok": True})

    @action(detail=False, methods=["post"], url_path="bulk")
    def bulk(self, request):
        return error_response(
            code=ErrorCode.VALIDATION_FAILED,
            message="Some rows failed.",
            details={"failed_rows": [1, 2]},
        )


_router = SimpleRouter()
_router.register(r"contract", _ContractViewSet, basename="contract")


class APIErrorContractTests(URLPatternsTestCase):
    """Mounts a throwaway router so we exercise the real DRF dispatch path —
    URL routing, content negotiation, exception handler — end-to-end."""

    urlpatterns = [path("api/_test/", include(_router.urls))]

    def setUp(self):
        self.client = APIClient()

    def _assert_envelope(self, body, *, code, has_details=None):
        assert "error" in body, body
        err = body["error"]
        assert err["code"] == code, err
        assert isinstance(err["message"], str) and err["message"]
        if has_details is True:
            assert "details" in err, err
        elif has_details is False:
            assert "details" not in err, err

    def test_validation_error_uses_envelope(self):
        resp = self.client.post("/api/_test/contract/validate/", {}, format="json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        body = resp.json()
        self._assert_envelope(body, code=ErrorCode.VALIDATION_FAILED, has_details=True)
        assert "email" in body["error"]["details"]

    def test_permission_denied_uses_envelope(self):
        resp = self.client.get("/api/_test/contract/forbidden/")
        assert resp.status_code == status.HTTP_403_FORBIDDEN
        body = resp.json()
        self._assert_envelope(body, code=ErrorCode.PERMISSION_DENIED)
        assert body["error"]["message"] == "You cannot do that."

    def test_unauthenticated_uses_envelope(self):
        resp = self.client.get("/api/_test/contract/needs-auth/")
        assert resp.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
        body = resp.json()
        assert body["error"]["code"] in (
            ErrorCode.NOT_AUTHENTICATED,
            ErrorCode.PERMISSION_DENIED,
        )

    def test_not_found_uses_envelope(self):
        resp = self.client.get("/api/_test/contract/missing/")
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        self._assert_envelope(resp.json(), code=ErrorCode.NOT_FOUND)

    def test_dependency_unavailable_uses_envelope(self):
        resp = self.client.get("/api/_test/contract/dep-down/")
        assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        body = resp.json()
        self._assert_envelope(body, code=ErrorCode.DEPENDENCY_UNAVAILABLE, has_details=True)
        assert body["error"]["details"]["dependency"] == "redis"

    def test_task_queued_uses_envelope(self):
        resp = self.client.post("/api/_test/contract/async-task/")
        assert resp.status_code == status.HTTP_202_ACCEPTED
        body = resp.json()
        self._assert_envelope(body, code=ErrorCode.TASK_QUEUED, has_details=True)
        assert body["error"]["details"]["task_id"] == "abc-123"

    def test_throttled_uses_envelope_and_retry_after(self):
        self.client.get("/api/_test/contract/throttled/")
        resp = self.client.get("/api/_test/contract/throttled/")
        assert resp.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        body = resp.json()
        self._assert_envelope(body, code=ErrorCode.THROTTLED, has_details=True)
        assert "retry_after_seconds" in body["error"]["details"]

    def test_conflict_uses_envelope(self):
        resp = self.client.post("/api/_test/contract/conflict/")
        assert resp.status_code == status.HTTP_409_CONFLICT
        body = resp.json()
        self._assert_envelope(body, code=ErrorCode.CONFLICT)
        assert body["error"]["message"] == "Resource is locked."

    def test_method_not_allowed_uses_envelope(self):
        resp = self.client.delete("/api/_test/contract/validate/")
        assert resp.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
        self._assert_envelope(resp.json(), code=ErrorCode.METHOD_NOT_ALLOWED)

    def test_error_response_helper_envelope(self):
        resp = self.client.post("/api/_test/contract/bulk/")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        body = resp.json()
        self._assert_envelope(body, code=ErrorCode.VALIDATION_FAILED, has_details=True)
        assert body["error"]["details"] == {"failed_rows": [1, 2]}


@pytest.mark.django_db
def test_django_validation_error_is_translated():
    """A bare Django ValidationError must be reshaped into the envelope so that
    serializers / model-clean code paths produce the same shape DRF does."""
    from django.core.exceptions import ValidationError as DjangoValidationError

    from config.api_errors import standardized_exception_handler

    exc = DjangoValidationError({"name": ["This field cannot be blank."]})
    response = standardized_exception_handler(exc, context={})
    assert response is not None
    assert response.status_code == 400
    assert response.data["error"]["code"] == ErrorCode.VALIDATION_FAILED
    assert "name" in response.data["error"]["details"]


def test_unhandled_exception_returns_none():
    """Plain Python exceptions must fall through so Django's 500 handler runs."""
    from config.api_errors import standardized_exception_handler

    assert standardized_exception_handler(RuntimeError("boom"), context={}) is None
