"""
Idempotently configure EMQX's JWT authenticator against the OMS JWKS endpoint.

EMQX needs to know how to verify device JWTs before any device can connect.
This command POSTs the JWT-via-JWKS authenticator definition to the EMQX REST
API (``EMQX_API_URL``) using ``EMQX_API_KEY`` / ``EMQX_API_SECRET`` for HTTP
basic auth. If a JWT authenticator already exists on the default
authentication chain, the command skips the create — re-runs are safe.

By default the command also disables anonymous MQTT connections so EMQX cannot
fall back to insecure anonymous access; pass ``--keep-anonymous`` to skip that
step (useful in dev rigs).

The "disable anonymous" step is version-aware (oms-779):

* EMQX 4.x exposes a global ``mqtt.allow_anonymous`` flag at
  ``PUT /configs/mqtt``.
* EMQX 5.x removed that endpoint; the equivalent is per-listener
  ``enable_authn=true``, which forces every connecting client through the
  authentication chain (denying anonymous when no authenticator matches).

Run after a fresh EMQX deploy or whenever the JWKS URL changes:

    python manage.py configure_emqx_jwt_auth \
        --jwks-url https://oms.example/api/forgekey/jwks/

Smoke-test without writing:

    python manage.py configure_emqx_jwt_auth --jwks-url ... --dry-run
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

import requests

logger = logging.getLogger(__name__)


JWT_AUTHENTICATOR_ID = "jwt:public-key"
DEFAULT_TIMEOUT_SECONDS = 10


class Command(BaseCommand):
    help = "Provision the EMQX JWT-via-JWKS authenticator from the OMS JWKS endpoint."

    def add_arguments(self, parser):
        parser.add_argument(
            "--jwks-url",
            required=True,
            help=(
                "Publicly reachable URL of the OMS JWKS endpoint, "
                "e.g. https://oms.example/api/forgekey/jwks/"
            ),
        )
        parser.add_argument(
            "--refresh-interval",
            type=int,
            default=300,
            help="Seconds EMQX caches the JWKS before re-fetching (default: 300).",
        )
        parser.add_argument(
            "--keep-anonymous",
            action="store_true",
            help="Do not disable anonymous MQTT connections (default: disable).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the requests that would be made without sending them.",
        )
        parser.add_argument(
            "--skip-jwks-check",
            action="store_true",
            help=(
                "Skip the JWKS-URL reachability pre-flight check (oms-zad). "
                "By default the command GETs --jwks-url before configuring "
                "EMQX so a 400/404 from a misconfigured ALLOWED_HOSTS fails "
                "fast with a clear error instead of silently breaking MQTT."
            ),
        )

    def handle(self, *args, **options):
        api_url = (getattr(settings, "EMQX_API_URL", "") or "").rstrip("/")
        api_key = getattr(settings, "EMQX_API_KEY", "") or ""
        api_secret = getattr(settings, "EMQX_API_SECRET", "") or ""
        if not api_url:
            raise CommandError("EMQX_API_URL is not configured")
        if not api_key or not api_secret:
            raise CommandError(
                "EMQX_API_KEY / EMQX_API_SECRET must be set; create them in the "
                "EMQX dashboard under API Keys before running this command."
            )

        jwks_url = options["jwks_url"].strip()
        refresh = int(options["refresh_interval"])
        dry_run = bool(options["dry_run"])
        disable_anon = not bool(options["keep_anonymous"])
        skip_jwks_check = bool(options["skip_jwks_check"])

        verify_claims = {
            "iss": getattr(settings, "FORGEKEY_JWT_ISSUER", "openmakersuite"),
            "aud": getattr(settings, "FORGEKEY_JWT_AUDIENCE", "forgekey"),
        }
        authenticator_body = {
            "mechanism": "jwt",
            "use_jwks": True,
            "endpoint": jwks_url,
            "refresh_interval": refresh,
            "verify_claims": verify_claims,
            "acl_claim_name": "acl",
            "from": "password",
        }

        auth = (api_key, api_secret)
        if dry_run:
            self.stdout.write("[dry-run] EMQX requests that would be issued:")
            if not skip_jwks_check:
                self.stdout.write(f"  GET    {jwks_url}  # JWKS reachability pre-flight (oms-zad)")
            self.stdout.write(f"  GET    {api_url}/nodes  # detect EMQX version")
            self.stdout.write(f"  GET    {api_url}/authentication")
            self.stdout.write(f"  POST   {api_url}/authentication  body={authenticator_body}")
            if disable_anon:
                self.stdout.write("  (4.x) PUT    /configs/mqtt  body={'allow_anonymous': False}")
                self.stdout.write(
                    "  (5.x) GET    /listeners; PUT /listeners/<id>  body+={'enable_authn': True}"
                )
            return

        if not skip_jwks_check:
            self._verify_jwks_reachable(jwks_url)
            self.stdout.write(self.style.SUCCESS(f"JWKS endpoint reachable at {jwks_url}."))

        major, version = self._emqx_version(api_url, auth)
        self.stdout.write(self.style.NOTICE(f"EMQX {version} detected (major={major})."))

        existing = self._existing_jwt_authenticator(api_url, auth)
        if existing is not None:
            self.stdout.write(
                self.style.NOTICE(
                    f"JWT authenticator already present (id={existing.get('id', '?')}); skipping create."
                )
            )
        else:
            self._create_authenticator(api_url, auth, authenticator_body)
            self.stdout.write(self.style.SUCCESS("JWT authenticator created."))

        if disable_anon:
            if major >= 5:
                changed = self._disable_anonymous_v5(api_url, auth)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"EMQX 5.x: enable_authn=true on {changed} listener(s); "
                        "anonymous connects will be denied via the auth chain."
                    )
                )
            else:
                self._disable_anonymous_v4(api_url, auth)
                self.stdout.write(
                    self.style.SUCCESS("EMQX 4.x: mqtt.allow_anonymous set to false.")
                )
        else:
            self.stdout.write(self.style.WARNING("Skipped disabling anonymous connections."))

    @staticmethod
    def _verify_jwks_reachable(jwks_url: str) -> None:
        """Pre-flight: GET ``jwks_url`` and fail with a clear hint on non-200.

        The 400 we hit on a fresh deploy comes from Django rejecting the
        ``Host: backend:8000`` header EMQX sends because ``ALLOWED_HOSTS`` is
        missing the docker service name. Detect that before writing config so
        operators see the cause instead of an opaque MQTT auth failure.
        """
        try:
            resp = requests.get(jwks_url, timeout=DEFAULT_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            raise CommandError(
                f"JWKS pre-flight: GET {jwks_url} raised {exc.__class__.__name__}: {exc}. "
                "Verify the URL is reachable from this container; if EMQX runs "
                "on the same docker network use http://backend:8000/api/forgekey/jwks/."
            ) from exc

        if resp.status_code == 400:
            body = (resp.text or "").strip()[:200]
            raise CommandError(
                f"JWKS pre-flight: GET {jwks_url} returned 400 (likely "
                "ALLOWED_HOSTS misconfiguration). Add the Host header value "
                "(e.g. `backend`) to ALLOWED_HOSTS in .env and redeploy. "
                f"Response body: {body}"
            )
        if resp.status_code != 200:
            body = (resp.text or "").strip()[:200]
            raise CommandError(
                f"JWKS pre-flight: GET {jwks_url} returned {resp.status_code}; "
                "EMQX would not be able to fetch the public key. "
                f"Response body: {body}"
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise CommandError(
                f"JWKS pre-flight: GET {jwks_url} did not return JSON: {exc}. "
                "Ensure the URL points at /api/forgekey/jwks/, not the SPA."
            ) from exc

        keys = (payload or {}).get("keys") if isinstance(payload, dict) else None
        if not keys:
            raise CommandError(
                f"JWKS pre-flight: GET {jwks_url} returned JSON without a "
                "non-empty `keys` array; FORGEKEY_JWT_SIGNING_KEY may be unset."
            )

    @staticmethod
    def _emqx_version(api_url: str, auth) -> tuple[int, str]:
        """Return ``(major_version_int, raw_version_string)`` from ``GET /nodes``.

        EMQX 5.x and 4.x both expose ``GET /nodes`` returning a list of node
        dicts with a ``version`` field like ``"5.4.1"`` or ``"4.4.19"``.
        """
        resp = requests.get(f"{api_url}/nodes", auth=auth, timeout=DEFAULT_TIMEOUT_SECONDS)
        if resp.status_code != 200:
            raise CommandError(
                f"GET /nodes failed (cannot detect EMQX version): "
                f"{resp.status_code} {resp.text[:200]}"
            )
        try:
            payload = resp.json()
        except ValueError as exc:
            raise CommandError(f"GET /nodes did not return JSON: {exc}") from exc
        nodes = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not nodes:
            raise CommandError("GET /nodes returned an empty list; cannot detect EMQX version.")
        version = (nodes[0] or {}).get("version") or ""
        if not version:
            raise CommandError(f"GET /nodes node entry has no 'version' field: {nodes[0]!r}")
        head = version.lstrip("vV").split(".", 1)[0]
        try:
            major = int(head)
        except ValueError as exc:
            raise CommandError(
                f"Could not parse major version from EMQX version string '{version}': {exc}"
            ) from exc
        return major, version

    @staticmethod
    def _existing_jwt_authenticator(api_url: str, auth) -> dict | None:
        resp = requests.get(f"{api_url}/authentication", auth=auth, timeout=DEFAULT_TIMEOUT_SECONDS)
        if resp.status_code != 200:
            raise CommandError(f"GET /authentication failed: {resp.status_code} {resp.text[:200]}")
        try:
            entries = resp.json()
        except ValueError as exc:
            raise CommandError(f"GET /authentication did not return JSON: {exc}") from exc
        for entry in entries or []:
            if entry.get("mechanism") == "jwt":
                return entry
        return None

    @staticmethod
    def _create_authenticator(api_url: str, auth, body: dict) -> None:
        resp = requests.post(
            f"{api_url}/authentication",
            auth=auth,
            json=body,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        if resp.status_code not in (200, 201, 204):
            raise CommandError(f"POST /authentication failed: {resp.status_code} {resp.text[:200]}")

    @staticmethod
    def _disable_anonymous_v4(api_url: str, auth) -> None:
        resp = requests.put(
            f"{api_url}/configs/mqtt",
            auth=auth,
            json={"allow_anonymous": False},
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        if resp.status_code not in (200, 204):
            raise CommandError(
                f"PUT /configs/mqtt failed (EMQX 4.x path): "
                f"{resp.status_code} {resp.text[:200]}"
            )

    @staticmethod
    def _disable_anonymous_v5(api_url: str, auth) -> int:
        """Force per-listener authentication on EMQX 5.x.

        Returns the count of listeners that needed an update (0 if all were
        already enforcing auth). Re-runs are idempotent: listeners already at
        ``enable_authn=true`` are skipped.
        """
        resp = requests.get(f"{api_url}/listeners", auth=auth, timeout=DEFAULT_TIMEOUT_SECONDS)
        if resp.status_code != 200:
            raise CommandError(
                f"GET /listeners failed (EMQX 5.x path): " f"{resp.status_code} {resp.text[:200]}"
            )
        try:
            listeners = resp.json()
        except ValueError as exc:
            raise CommandError(f"GET /listeners did not return JSON: {exc}") from exc
        if isinstance(listeners, dict):
            listeners = listeners.get("data", [])
        if not listeners:
            raise CommandError(
                "GET /listeners returned no listeners; cannot enforce authentication on EMQX 5.x."
            )

        changed = 0
        for listener in listeners:
            lid = listener.get("id")
            if not lid:
                continue
            # quick_deny_anonymous and true both deny anonymous; only false leaves
            # the door open. Treat anything other than False as already-enforcing.
            current = listener.get("enable_authn", True)
            if current is not False:
                continue
            merged = dict(listener)
            merged["enable_authn"] = True
            put = requests.put(
                f"{api_url}/listeners/{lid}",
                auth=auth,
                json=merged,
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
            if put.status_code not in (200, 204):
                raise CommandError(
                    f"PUT /listeners/{lid} failed (EMQX 5.x path): "
                    f"{put.status_code} {put.text[:200]}"
                )
            changed += 1
        return changed
