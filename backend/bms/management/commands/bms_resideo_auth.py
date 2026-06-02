"""
Interactive OAuth bootstrap for a Resideo BMS config.

Usage:
    python manage.py bms_resideo_auth --name "Main shop Honeywell"

Walks the operator through the authorization_code OAuth flow:
    1. Creates (or reuses) a BmsConfig row with adapter_type=resideo.
    2. Prints the authorize URL — operator opens it in a browser, logs
       into their Honeywell Home account, and authorizes the OMS app.
    3. After the redirect, the URL bar carries ?code=... — operator
       pastes that code (or the full URL) on stdin.
    4. Command exchanges the code for an access + refresh token pair
       and persists them encrypted on the BmsConfig row.

The redirect URI must exactly match what's registered on the Resideo
app at developer.resideo.com. Defaults to settings.RESIDEO_REDIRECT_URI,
override with --redirect-uri.
"""

from __future__ import annotations

import re
import secrets

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ...adapters import BmsAdapterError, ResideoAdapter
from ...models import BmsConfig


class Command(BaseCommand):
    help = "Bootstrap or refresh Resideo OAuth tokens for a BmsConfig."

    def add_arguments(self, parser):
        parser.add_argument(
            "--name",
            required=True,
            help="Name of the BmsConfig row to create / update.",
        )
        parser.add_argument(
            "--redirect-uri",
            default="",
            help="OAuth redirect URI. Must match the value registered on the "
            "Resideo app exactly. Defaults to settings.RESIDEO_REDIRECT_URI.",
        )

    def handle(self, *args, **options):
        name = options["name"]
        redirect_uri = (
            options["redirect_uri"] or getattr(settings, "RESIDEO_REDIRECT_URI", "") or ""
        )
        if not redirect_uri:
            raise CommandError(
                "No redirect URI: pass --redirect-uri or set "
                "RESIDEO_REDIRECT_URI in the backend env."
            )
        if not getattr(settings, "RESIDEO_CLIENT_ID", ""):
            raise CommandError(
                "RESIDEO_CLIENT_ID is not set on the backend. Register an "
                "app at https://developer.resideo.com and set the client_id "
                "+ client_secret in the prod .env first."
            )

        config, created = BmsConfig.objects.get_or_create(
            name=name,
            defaults={"adapter_type": BmsConfig.ADAPTER_RESIDEO},
        )
        if not created and config.adapter_type != BmsConfig.ADAPTER_RESIDEO:
            raise CommandError(
                f"BmsConfig {name!r} already exists with "
                f"adapter_type={config.adapter_type!r}; refusing to mutate."
            )

        state = secrets.token_urlsafe(16)
        url = ResideoAdapter.authorize_url(redirect_uri, state=state)
        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "1. Open this URL in a browser logged into the Honeywell Home "
                "account that owns the thermostats:"
            )
        )
        self.stdout.write("")
        self.stdout.write(f"   {url}")
        self.stdout.write("")
        self.stdout.write("2. Authorize the OMS app. The browser will redirect to:")
        self.stdout.write(f"   {redirect_uri}?code=<...>&state={state}")
        self.stdout.write("")
        self.stdout.write("3. Paste the FULL redirected URL (or just the code value) below.")
        self.stdout.write("")
        raw = input("code or URL: ").strip()
        if not raw:
            raise CommandError("No code provided; aborting.")

        code = _extract_code(raw)
        if not code:
            raise CommandError(f"Couldn't extract a 'code=' parameter from {raw!r}.")

        adapter = ResideoAdapter(config)
        try:
            adapter.exchange_authorization_code(code, redirect_uri)
        except BmsAdapterError as exc:
            raise CommandError(f"Token exchange failed: {exc}") from exc

        config.refresh_from_db()
        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"OK — tokens persisted on BmsConfig {name!r}. "
                f"Access expires at {config.access_token_expires_at.isoformat()}."
            )
        )
        self.stdout.write(
            "Run `python manage.py bms_sync_all --config " f'"{name}"` to discover thermostats.'
        )


def _extract_code(raw: str) -> str:
    """Accept either a bare code or a full redirected URL."""
    m = re.search(r"[?&]code=([^&\s]+)", raw)
    if m:
        return m.group(1)
    # Bare code path — Resideo codes are URL-safe; pass through if it
    # doesn't look like a URL.
    if "://" not in raw and "&" not in raw:
        return raw
    return ""
