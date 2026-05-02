"""
Tests for the configure_emqx_jwt_auth management command (oms-6h1).
"""

from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError

import pytest

JWKS_URL = "https://oms.example/api/forgekey/jwks/"


def _settings_present(settings):
    settings.EMQX_API_URL = "http://emqx:18083/api/v5"
    settings.EMQX_API_KEY = "key"
    settings.EMQX_API_SECRET = "secret"


def _ok(json_value=None, status_code=200):
    resp = mock.Mock()
    resp.status_code = status_code
    resp.json.return_value = json_value if json_value is not None else []
    resp.text = ""
    return resp


@pytest.mark.unit
class TestConfigureEmqxJwtAuth:
    def test_requires_emqx_api_url(self, settings):
        settings.EMQX_API_URL = ""
        settings.EMQX_API_KEY = "k"
        settings.EMQX_API_SECRET = "s"
        with pytest.raises(CommandError, match="EMQX_API_URL"):
            call_command("configure_emqx_jwt_auth", f"--jwks-url={JWKS_URL}")

    def test_requires_credentials(self, settings):
        settings.EMQX_API_URL = "http://emqx:18083/api/v5"
        settings.EMQX_API_KEY = ""
        settings.EMQX_API_SECRET = ""
        with pytest.raises(CommandError, match="EMQX_API_KEY"):
            call_command("configure_emqx_jwt_auth", f"--jwks-url={JWKS_URL}")

    def test_dry_run_makes_no_http_calls(self, settings):
        _settings_present(settings)
        with mock.patch("forgekey.management.commands.configure_emqx_jwt_auth.requests") as req:
            call_command(
                "configure_emqx_jwt_auth",
                f"--jwks-url={JWKS_URL}",
                "--dry-run",
                stdout=StringIO(),
            )
        req.get.assert_not_called()
        req.post.assert_not_called()
        req.put.assert_not_called()

    def test_creates_authenticator_when_none_exists(self, settings):
        _settings_present(settings)
        with mock.patch("forgekey.management.commands.configure_emqx_jwt_auth.requests") as req:
            req.get.return_value = _ok([])  # no existing authenticators
            req.post.return_value = _ok(status_code=201)
            req.put.return_value = _ok(status_code=204)
            call_command("configure_emqx_jwt_auth", f"--jwks-url={JWKS_URL}", stdout=StringIO())
        # POSTed the JWT body with the JWKS endpoint.
        post_call = req.post.call_args
        assert post_call.kwargs["json"]["mechanism"] == "jwt"
        assert post_call.kwargs["json"]["use_jwks"] is True
        assert post_call.kwargs["json"]["endpoint"] == JWKS_URL
        assert post_call.kwargs["json"]["acl_claim_name"] == "acl"
        assert post_call.kwargs["json"]["from"] == "password"
        assert post_call.kwargs["json"]["verify_claims"] == {
            "iss": settings.FORGEKEY_JWT_ISSUER,
            "aud": settings.FORGEKEY_JWT_AUDIENCE,
        }
        # Disabled anonymous by default.
        put_call = req.put.call_args
        assert put_call.kwargs["json"] == {"allow_anonymous": False}

    def test_skips_create_when_jwt_authenticator_already_exists(self, settings):
        _settings_present(settings)
        with mock.patch("forgekey.management.commands.configure_emqx_jwt_auth.requests") as req:
            req.get.return_value = _ok([{"id": "jwt:abc", "mechanism": "jwt"}])
            req.put.return_value = _ok(status_code=204)
            call_command("configure_emqx_jwt_auth", f"--jwks-url={JWKS_URL}", stdout=StringIO())
        req.post.assert_not_called()  # idempotent: no duplicate create
        req.put.assert_called_once()  # still toggles allow_anonymous

    def test_keep_anonymous_skips_put(self, settings):
        _settings_present(settings)
        with mock.patch("forgekey.management.commands.configure_emqx_jwt_auth.requests") as req:
            req.get.return_value = _ok([])
            req.post.return_value = _ok(status_code=201)
            call_command(
                "configure_emqx_jwt_auth",
                f"--jwks-url={JWKS_URL}",
                "--keep-anonymous",
                stdout=StringIO(),
            )
        req.put.assert_not_called()

    def test_authentication_get_failure_raises(self, settings):
        _settings_present(settings)
        with mock.patch("forgekey.management.commands.configure_emqx_jwt_auth.requests") as req:
            err = mock.Mock()
            err.status_code = 401
            err.text = "unauthorized"
            req.get.return_value = err
            with pytest.raises(CommandError, match="GET /authentication"):
                call_command(
                    "configure_emqx_jwt_auth",
                    f"--jwks-url={JWKS_URL}",
                    stdout=StringIO(),
                )
