"""
Idempotently configure EMQX's JWT authenticator against the OMS JWKS endpoint.

EMQX needs to know how to verify device JWTs before any device can connect.
This command POSTs the JWT-via-JWKS authenticator definition to the EMQX REST
API (``EMQX_API_URL``) using ``EMQX_API_KEY`` / ``EMQX_API_SECRET`` for HTTP
basic auth. If a JWT authenticator already exists on the default
authentication chain, the command skips the create — re-runs are safe.

By default the command also flips the broker's ``allow_anonymous`` flag to
``false`` so EMQX cannot fall back to insecure anonymous connections; pass
``--keep-anonymous`` to skip that step (useful in dev rigs).

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
            help="Do not disable mqtt.allow_anonymous (default: disable).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the requests that would be made without sending them.",
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
            self.stdout.write(f"  GET    {api_url}/authentication")
            self.stdout.write(f"  POST   {api_url}/authentication  body={authenticator_body}")
            if disable_anon:
                self.stdout.write(
                    f"  PUT    {api_url}/configs/mqtt  body={{'allow_anonymous': False}}"
                )
            return

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
            self._disable_anonymous(api_url, auth)
            self.stdout.write(self.style.SUCCESS("mqtt.allow_anonymous set to false."))
        else:
            self.stdout.write(self.style.WARNING("Skipped disabling mqtt.allow_anonymous."))

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
    def _disable_anonymous(api_url: str, auth) -> None:
        resp = requests.put(
            f"{api_url}/configs/mqtt",
            auth=auth,
            json={"allow_anonymous": False},
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        if resp.status_code not in (200, 204):
            raise CommandError(f"PUT /configs/mqtt failed: {resp.status_code} {resp.text[:200]}")
