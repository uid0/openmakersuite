"""
Tests for the configure_emqx_jwt_auth management command (oms-6h1, oms-779).
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


def _resp(json_value=None, status_code=200, text=""):
    resp = mock.Mock()
    resp.status_code = status_code
    resp.json.return_value = json_value if json_value is not None else []
    resp.text = text
    return resp


def _v5_nodes(version="5.4.1"):
    return _resp([{"node": "emqx@127.0.0.1", "version": version}])


def _v4_nodes(version="4.4.19"):
    return _resp([{"node": "emqx@127.0.0.1", "version": version}])


def _v5_listeners(items=None):
    if items is None:
        items = [
            {"id": "tcp:default", "type": "tcp", "name": "default", "enable_authn": False},
            {"id": "ssl:default", "type": "ssl", "name": "default", "enable_authn": False},
        ]
    return _resp(items)


def _ok_jwks():
    return _resp({"keys": [{"kty": "EC", "crv": "P-256", "kid": "k1"}]})


def _route_get(api_url, *, nodes=None, listeners=None, authentication=None, jwks=None):
    """Build a side_effect for ``requests.get`` keyed by URL suffix."""
    nodes = nodes if nodes is not None else _v5_nodes()
    listeners = listeners if listeners is not None else _v5_listeners()
    authentication = authentication if authentication is not None else _resp([])
    jwks = jwks if jwks is not None else _ok_jwks()

    def _side_effect(url, *args, **kwargs):
        if url.endswith("/nodes"):
            return nodes
        if url.endswith("/listeners"):
            return listeners
        if url.endswith("/authentication"):
            return authentication
        if "/jwks" in url:
            return jwks
        raise AssertionError(f"unexpected GET to {url}")

    return _side_effect


def _route_put(*, mqtt=None, listener=None):
    mqtt = mqtt if mqtt is not None else _resp(status_code=204)
    listener = listener if listener is not None else _resp(status_code=204)

    def _side_effect(url, *args, **kwargs):
        if "/configs/mqtt" in url:
            return mqtt
        if "/listeners/" in url:
            return listener
        raise AssertionError(f"unexpected PUT to {url}")

    return _side_effect


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

    def test_v5_creates_authenticator_and_enforces_listener_authn(self, settings):
        """AC-1: 5.x flow disables anonymous via per-listener enable_authn."""
        _settings_present(settings)
        with mock.patch("forgekey.management.commands.configure_emqx_jwt_auth.requests") as req:
            req.get.side_effect = _route_get(settings.EMQX_API_URL)
            req.post.return_value = _resp(status_code=201)
            req.put.side_effect = _route_put()
            stdout = StringIO()
            call_command(
                "configure_emqx_jwt_auth",
                f"--jwks-url={JWKS_URL}",
                stdout=stdout,
            )
        # POSTed the JWT body with the JWKS endpoint.
        post_call = req.post.call_args
        assert post_call.kwargs["json"]["mechanism"] == "jwt"
        assert post_call.kwargs["json"]["use_jwks"] is True
        assert post_call.kwargs["json"]["endpoint"] == JWKS_URL
        # oms-4jw: default refresh_interval must be short enough that EMQX
        # recovers from a transient nxdomain (backend bouncing) within the
        # next refresh tick instead of staying broken for the legacy 300s.
        assert post_call.kwargs["json"]["refresh_interval"] == 30
        # On 5.x we never PUT /configs/mqtt — that endpoint is gone.
        for put_call in req.put.call_args_list:
            assert "/configs/mqtt" not in put_call.args[0]
        # Each listener got PUT /listeners/<id> with enable_authn=true merged in.
        listener_puts = [c for c in req.put.call_args_list if "/listeners/" in c.args[0]]
        assert len(listener_puts) == 2
        for c in listener_puts:
            assert c.kwargs["json"]["enable_authn"] is True
        # AC-4: success message names the version.
        out = stdout.getvalue()
        assert "5.4.1" in out
        assert "EMQX 5.x" in out

    def test_refresh_interval_flag_overrides_default(self, settings):
        """oms-4jw: --refresh-interval is forwarded to EMQX so operators can tune."""
        _settings_present(settings)
        with mock.patch("forgekey.management.commands.configure_emqx_jwt_auth.requests") as req:
            req.get.side_effect = _route_get(settings.EMQX_API_URL)
            req.post.return_value = _resp(status_code=201)
            req.put.side_effect = _route_put()
            call_command(
                "configure_emqx_jwt_auth",
                f"--jwks-url={JWKS_URL}",
                "--refresh-interval=15",
                stdout=StringIO(),
            )
        assert req.post.call_args.kwargs["json"]["refresh_interval"] == 15

    def test_v5_idempotent_when_listeners_already_enforce_authn(self, settings):
        """AC-2: re-running on 5.x with auth already enforced does not error or PUT."""
        _settings_present(settings)
        listeners = _v5_listeners(
            [
                {"id": "tcp:default", "type": "tcp", "name": "default", "enable_authn": True},
                {
                    "id": "ssl:default",
                    "type": "ssl",
                    "name": "default",
                    "enable_authn": "quick_deny_anonymous",
                },
            ]
        )
        existing_authn = _resp([{"id": "jwt:abc", "mechanism": "jwt"}])
        with mock.patch("forgekey.management.commands.configure_emqx_jwt_auth.requests") as req:
            req.get.side_effect = _route_get(
                settings.EMQX_API_URL,
                listeners=listeners,
                authentication=existing_authn,
            )
            req.put.side_effect = _route_put()
            call_command(
                "configure_emqx_jwt_auth",
                f"--jwks-url={JWKS_URL}",
                stdout=StringIO(),
            )
        req.post.assert_not_called()  # authenticator already exists
        req.put.assert_not_called()  # listeners already enforce auth

    def test_v4_uses_legacy_configs_mqtt_endpoint(self, settings):
        """AC-3: 4.x flow falls back to PUT /configs/mqtt."""
        _settings_present(settings)
        with mock.patch("forgekey.management.commands.configure_emqx_jwt_auth.requests") as req:
            req.get.side_effect = _route_get(
                settings.EMQX_API_URL,
                nodes=_v4_nodes(),
            )
            req.post.return_value = _resp(status_code=201)
            req.put.side_effect = _route_put()
            stdout = StringIO()
            call_command(
                "configure_emqx_jwt_auth",
                f"--jwks-url={JWKS_URL}",
                stdout=stdout,
            )
        # Exactly one PUT, hitting the legacy 4.x endpoint.
        assert req.put.call_count == 1
        put_call = req.put.call_args
        assert put_call.args[0].endswith("/configs/mqtt")
        assert put_call.kwargs["json"] == {"allow_anonymous": False}
        # AC-4: success message mentions 4.x.
        assert "EMQX 4.x" in stdout.getvalue()
        assert "4.4.19" in stdout.getvalue()

    def test_skips_create_when_jwt_authenticator_already_exists(self, settings):
        _settings_present(settings)
        existing_authn = _resp([{"id": "jwt:abc", "mechanism": "jwt"}])
        with mock.patch("forgekey.management.commands.configure_emqx_jwt_auth.requests") as req:
            req.get.side_effect = _route_get(
                settings.EMQX_API_URL,
                authentication=existing_authn,
            )
            req.put.side_effect = _route_put()
            call_command("configure_emqx_jwt_auth", f"--jwks-url={JWKS_URL}", stdout=StringIO())
        req.post.assert_not_called()  # idempotent: no duplicate create
        # Listeners still get updated since they were enable_authn=False.
        assert any("/listeners/" in c.args[0] for c in req.put.call_args_list)

    def test_keep_anonymous_skips_disable_step(self, settings):
        _settings_present(settings)
        with mock.patch("forgekey.management.commands.configure_emqx_jwt_auth.requests") as req:
            req.get.side_effect = _route_get(settings.EMQX_API_URL)
            req.post.return_value = _resp(status_code=201)
            req.put.side_effect = _route_put()
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
            req.get.side_effect = _route_get(
                settings.EMQX_API_URL,
                authentication=_resp(status_code=401, text="unauthorized"),
            )
            with pytest.raises(CommandError, match="GET /authentication"):
                call_command(
                    "configure_emqx_jwt_auth",
                    f"--jwks-url={JWKS_URL}",
                    stdout=StringIO(),
                )

    def test_nodes_failure_raises_with_version_context(self, settings):
        """AC-4: failure to detect version surfaces a clear error."""
        _settings_present(settings)
        with mock.patch("forgekey.management.commands.configure_emqx_jwt_auth.requests") as req:
            req.get.side_effect = _route_get(
                settings.EMQX_API_URL,
                nodes=_resp(status_code=403, text="forbidden"),
            )
            with pytest.raises(CommandError, match="GET /nodes failed"):
                call_command(
                    "configure_emqx_jwt_auth",
                    f"--jwks-url={JWKS_URL}",
                    stdout=StringIO(),
                )

    def test_v5_listener_put_failure_message_is_specific(self, settings):
        """AC-4: listener PUT failure points at the EMQX 5.x path explicitly."""
        _settings_present(settings)
        with mock.patch("forgekey.management.commands.configure_emqx_jwt_auth.requests") as req:
            req.get.side_effect = _route_get(settings.EMQX_API_URL)
            req.post.return_value = _resp(status_code=201)
            req.put.side_effect = _route_put(
                listener=_resp(status_code=400, text="bad config"),
            )
            with pytest.raises(CommandError, match=r"PUT /listeners/.*EMQX 5\.x"):
                call_command(
                    "configure_emqx_jwt_auth",
                    f"--jwks-url={JWKS_URL}",
                    stdout=StringIO(),
                )


@pytest.mark.unit
class TestJwksPreflight:
    """oms-zad: pre-flight JWKS reachability check before configuring EMQX."""

    def test_jwks_400_blocks_with_allowed_hosts_hint(self, settings):
        _settings_present(settings)
        with mock.patch("forgekey.management.commands.configure_emqx_jwt_auth.requests") as req:
            req.get.side_effect = _route_get(
                settings.EMQX_API_URL,
                jwks=_resp(status_code=400, text="<html>Bad Request (400)</html>"),
            )
            with pytest.raises(CommandError, match=r"ALLOWED_HOSTS"):
                call_command(
                    "configure_emqx_jwt_auth",
                    f"--jwks-url={JWKS_URL}",
                    stdout=StringIO(),
                )
            req.post.assert_not_called()  # never wrote EMQX config

    def test_jwks_503_blocks_with_status_code(self, settings):
        _settings_present(settings)
        with mock.patch("forgekey.management.commands.configure_emqx_jwt_auth.requests") as req:
            req.get.side_effect = _route_get(
                settings.EMQX_API_URL,
                jwks=_resp(status_code=503, text="signing key not configured"),
            )
            with pytest.raises(CommandError, match=r"returned 503"):
                call_command(
                    "configure_emqx_jwt_auth",
                    f"--jwks-url={JWKS_URL}",
                    stdout=StringIO(),
                )
            req.post.assert_not_called()

    def test_jwks_connection_error_surfaces_clear_error(self, settings):
        _settings_present(settings)
        import requests as real_requests  # noqa: WPS433

        def _boom(url, *a, **kw):
            if "/jwks" in url:
                raise real_requests.ConnectionError("name resolution failed")
            raise AssertionError(f"unexpected GET to {url}")

        with mock.patch("forgekey.management.commands.configure_emqx_jwt_auth.requests") as req:
            req.RequestException = real_requests.RequestException
            req.get.side_effect = _boom
            with pytest.raises(CommandError, match=r"JWKS pre-flight"):
                call_command(
                    "configure_emqx_jwt_auth",
                    f"--jwks-url={JWKS_URL}",
                    stdout=StringIO(),
                )
            req.post.assert_not_called()

    def test_jwks_empty_keys_blocks(self, settings):
        _settings_present(settings)
        with mock.patch("forgekey.management.commands.configure_emqx_jwt_auth.requests") as req:
            req.get.side_effect = _route_get(
                settings.EMQX_API_URL,
                jwks=_resp({"keys": []}),
            )
            with pytest.raises(CommandError, match=r"non-empty `keys` array"):
                call_command(
                    "configure_emqx_jwt_auth",
                    f"--jwks-url={JWKS_URL}",
                    stdout=StringIO(),
                )

    def test_skip_jwks_check_bypasses_preflight(self, settings):
        """--skip-jwks-check skips the GET so it doesn't fail on a 400."""
        _settings_present(settings)
        with mock.patch("forgekey.management.commands.configure_emqx_jwt_auth.requests") as req:
            # Provide good responses for everything except JWKS, which we
            # don't expect to be called.
            req.get.side_effect = _route_get(
                settings.EMQX_API_URL,
                jwks=_resp(status_code=400, text="should not be hit"),
            )
            req.post.return_value = _resp(status_code=201)
            req.put.side_effect = _route_put()
            call_command(
                "configure_emqx_jwt_auth",
                f"--jwks-url={JWKS_URL}",
                "--skip-jwks-check",
                stdout=StringIO(),
            )
            # Pre-flight skipped: only EMQX URLs hit, never the JWKS URL.
            for c in req.get.call_args_list:
                assert "/jwks" not in c.args[0]
            req.post.assert_called()  # configuration proceeded
