"""Tests for custom config middleware."""

from django.http import HttpResponse
from django.test import RequestFactory

import pytest

from config.middleware import PermissionsPolicyMiddleware


@pytest.mark.unit
class TestPermissionsPolicyMiddleware:
    """Verify Permissions-Policy header is set so getUserMedia works."""

    def test_sets_permissions_policy_header(self):
        request = RequestFactory().get("/")
        middleware = PermissionsPolicyMiddleware(lambda req: HttpResponse("ok"))

        response = middleware(request)

        assert response["Permissions-Policy"] == "camera=*, microphone=()"

    def test_does_not_overwrite_existing_header(self):
        """If a downstream view already set the header, respect it."""
        request = RequestFactory().get("/")

        def view(req):
            resp = HttpResponse("ok")
            resp["Permissions-Policy"] = "camera=()"
            return resp

        middleware = PermissionsPolicyMiddleware(view)
        response = middleware(request)

        assert response["Permissions-Policy"] == "camera=()"

    def test_header_present_through_django_test_client(self, client):
        """Sanity check that the middleware is wired into MIDDLEWARE."""
        response = client.get("/admin/login/")
        assert "Permissions-Policy" in response
        assert "camera=*" in response["Permissions-Policy"]
